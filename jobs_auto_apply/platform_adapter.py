"""Formal Platform Adapter contract (v3 §A6, §B4) + self-registering PlatformSpec.

Every existing v2 site package (``naukri/``, ``hirist/``, ``wellfound/``,
``instahyre/``, ``cutshort/``, ``uplers/``, ``indeed/``) already exposes
``apply_batch`` / ``collect_job_listings`` functions that used to be wired up by
hand in four separate places (config.py, browser.py, cli.py, platform_adapter.py).
This module now formalizes that implicit contract two ways:

1. ``PlatformAdapter`` — a ``runtime_checkable`` ``Protocol`` wrapping each site's
   existing functions in a thin adapter object (unchanged from the original v3
   pass — **no Playwright DOM logic is rewritten here**, only the seams are named).
2. ``PlatformSpec`` — a declarative, self-registering bundle of *everything* a
   platform needs to be wired up (origin/login URLs, auth method, the three
   adapter functions, its filters dataclass + YAML parser, etc). Each site
   package declares one module-level ``PLATFORM = PlatformSpec(...)`` constant
   (in its ``__init__.py``); ``discover_platforms()`` below finds it via
   ``pkgutil.iter_modules`` over ``jobs_auto_apply``'s immediate subpackages —
   any subpackage without a ``PLATFORM`` attribute (``answers/``, ``ats/``,
   ``tailoring/``, ``resilience/``, ``profile/``, ...) is silently skipped, not
   an error. Adding platform #8 now means writing that platform's package and
   declaring its ``PLATFORM`` spec — zero edits to this file, config.py,
   browser.py, or cli.py.

This is a pure, additive-to-the-*wiring* refactor: nothing here changes what any
existing CLI command does. The resilience engine (Phase 2) and the tailoring
shadow hook (Phase 3) are what actually read from ``ADAPTERS``.

Deviation from the SDA's literal §A6 signature (documented judgment call): the
spec's ``PlatformAdapter.collect_listings(config, limit)`` and
``apply(page, job, resume_path, config)`` omit ``page``/``context`` params that
the *real* underlying functions require (they operate on an already-open
Playwright page owned by the caller's session context manager, e.g.
``naukri_session()``). Rewriting every site package to construct its own page
would be exactly the "rewrite of underlying Playwright DOM logic" the prompt
says not to do. So adapters here accept ``page``/``context`` explicitly,
matching what the wrapped functions already need — the *shape* of the contract
(named methods, uniform signature across all six platforms, protocol-checked)
is what actually delivers OCP, not the exact parameter list.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from playwright.async_api import BrowserContext, Page

from .utils import JobListing

if TYPE_CHECKING:
    # Deliberately NOT imported at runtime: config.py's load_config() needs to
    # import discover_platforms() from *this* module to know which platform keys
    # to look for in YAML (Tier 1 step 6), so this module must not import
    # config.py eagerly at module scope or the two modules would deadlock each
    # other on import (config -> platform_adapter -> config, mid-definition).
    # `from __future__ import annotations` (above) already makes every type hint
    # in this file a lazy string, so this TYPE_CHECKING-only import costs nothing
    # at runtime while still giving type checkers/IDEs the real type.
    from .config import AppConfig

logger = logging.getLogger("job_apply")


class BlockingEventType:
    """Static resolvability table per §A5.2 (human-only vs bot-resolvable)."""

    CAPTCHA = "CAPTCHA"
    OTP_EMAIL = "OTP_EMAIL"
    GOOGLE_2FA_PUSH = "GOOGLE_2FA_PUSH"
    SESSION_DEAD = "SESSION_DEAD"
    TRANSIENT_DOM = "TRANSIENT_DOM"
    RATE_LIMITED = "RATE_LIMITED"

    HUMAN_ONLY = frozenset({CAPTCHA, OTP_EMAIL, GOOGLE_2FA_PUSH})
    BOT_RESOLVABLE = frozenset({TRANSIENT_DOM, RATE_LIMITED})


@dataclass
class BlockingEvent:
    type: str
    platform: str
    job_key: str = ""
    detail: str = ""


@runtime_checkable
class PlatformAdapter(Protocol):
    """Formal contract every job-board integration must satisfy (§A6)."""

    name: str

    async def collect_listings(self, page: Page, config: AppConfig, limit: int) -> list[JobListing]: ...

    async def apply(
        self,
        page: Page,
        context: BrowserContext | None,
        job: JobListing,
        resume_path,
        config: AppConfig,
    ) -> bool | None: ...

    async def verify_session(self, page: Page, config: AppConfig) -> bool: ...

    async def detect_blocking_event(
        self,
        page: Page,
        *,
        listings_found: int | None = None,
    ) -> BlockingEvent | None: ...


# ── generic, shared blocking-event detection (§A6: "safe generic fallback") ──

_CHALLENGE_TEXT_PATTERNS = re.compile(
    r"captcha|verify\s*it'?s\s*you|unusual\s*activity|are\s*you\s*a\s*robot|"
    r"suspicious\s*activity|too\s*many\s*requests|access\s*denied|rate\s*limit",
    re.I,
)
_OTP_TEXT_PATTERNS = re.compile(r"one[\s-]*time\s*(password|code)|otp\s*sent|enter\s*(the\s*)?otp", re.I)
_2FA_TEXT_PATTERNS = re.compile(
    r"2-?step\s*verification|two[\s-]*factor|google\s*sent\s*a\s*notification|check\s*your\s*(phone|device)",
    re.I,
)
_SESSION_DEAD_PATTERNS = re.compile(r"sign\s*in\s*to\s*continue|session\s*expired|please\s*log\s*in|log\s*back\s*in", re.I)


async def _page_text_safe(page: Page, limit: int = 4000) -> str:
    try:
        text = await page.inner_text("body", timeout=1500)
        return (text or "")[:limit]
    except Exception:
        return ""


async def generic_detect_blocking_event(
    page: Page,
    *,
    platform: str,
    listings_found: int | None = None,
) -> BlockingEvent | None:
    """Shared, safe-default challenge detector used by all six adapters.

    Checks the current page for common challenge indicators: text patterns
    ("captcha", "verify it's you", "unusual activity"), a recaptcha/hcaptcha
    iframe, and 429-style rate-limit text. Site-specific adapters can layer
    additional checks on top (e.g. Wellfound's §B5 refinement).
    """
    try:
        for frame in page.frames:
            url = (frame.url or "").lower()
            if "recaptcha" in url or "hcaptcha" in url:
                return BlockingEvent(type=BlockingEventType.CAPTCHA, platform=platform, detail=url)
    except Exception:
        pass

    text = await _page_text_safe(page)
    if not text:
        return None
    if _CHALLENGE_TEXT_PATTERNS.search(text):
        return BlockingEvent(type=BlockingEventType.CAPTCHA, platform=platform, detail="challenge text pattern")
    if _OTP_TEXT_PATTERNS.search(text):
        return BlockingEvent(type=BlockingEventType.OTP_EMAIL, platform=platform, detail="otp text pattern")
    if _2FA_TEXT_PATTERNS.search(text):
        return BlockingEvent(type=BlockingEventType.GOOGLE_2FA_PUSH, platform=platform, detail="2fa text pattern")
    if _SESSION_DEAD_PATTERNS.search(text):
        return BlockingEvent(type=BlockingEventType.SESSION_DEAD, platform=platform, detail="session-dead text pattern")
    return None


@dataclass
class _BaseAdapter:
    name: str
    _collect_fn: object = field(repr=False)
    _apply_batch_fn: object = field(repr=False)
    _verify_fn: object = field(repr=False)
    _collect_kwargs: dict = field(default_factory=dict, repr=False)

    async def collect_listings(self, page: Page, config: AppConfig, limit: int) -> list[JobListing]:
        return await self._collect_fn(page, limit, **self._collect_kwargs)

    async def apply(
        self,
        page: Page,
        context: BrowserContext | None,
        job: JobListing,
        resume_path,
        config: AppConfig,
    ) -> bool | None:
        # Thin wrapper: each site's apply_batch already does per-job apply +
        # question answering + result bookkeeping. Calling it with a single-job
        # list reuses that logic exactly rather than re-implementing it here.
        from .resume_context import use_resume_path

        with use_resume_path(resume_path):
            count = await self._apply_batch_fn(page, context, [job], config)
        return bool(count and count > 0)

    async def verify_session(self, page: Page, config: AppConfig) -> bool:
        return bool(await self._verify_fn(page, config.user.expected_display_name))

    async def detect_blocking_event(
        self,
        page: Page,
        *,
        listings_found: int | None = None,
    ) -> BlockingEvent | None:
        return await generic_detect_blocking_event(page, platform=self.name, listings_found=listings_found)


@dataclass
class _IndeedAdapter(_BaseAdapter):
    async def collect_listings(self, page: Page, config: AppConfig, limit: int) -> list[JobListing]:
        # §Issue 8: the CLI path (cli.py's _collect_jobs_for_platform / _run_indeed)
        # always passes easy_apply_only=filters.easy_apply_only explicitly. This
        # adapter's base collect_listings only forwards the static
        # ``_collect_kwargs`` frozen at registry build time (empty here, since no
        # config exists yet at that point) — so left unoverridden, this path
        # would silently use collect_job_listings' own default (False) instead of
        # the user's configured value. Read the live config at call time instead,
        # exactly like the CLI path does, so both paths agree.
        from .indeed.config import IndeedFiltersConfig

        filters = config.platforms["indeed"].filters
        kwargs = dict(self._collect_kwargs)
        if isinstance(filters, IndeedFiltersConfig):
            kwargs.setdefault("easy_apply_only", filters.easy_apply_only)
            kwargs.setdefault("domain", filters.domain)
        return await self._collect_fn(page, limit, **kwargs)


@dataclass
class _WellfoundAdapter(_BaseAdapter):
    async def detect_blocking_event(
        self,
        page: Page,
        *,
        listings_found: int | None = None,
    ) -> BlockingEvent | None:
        # §B5: a real, previously-observed failure mode during this project's own
        # Docker smoke test — headless Chromium (no real Chrome profile) verifies
        # as "logged in" but renders a signup/marketing page instead of the real
        # job search, silently reporting 0 listings on a normally-populated
        # search. Treat that combination as a soft-block signal.
        if listings_found == 0:
            return BlockingEvent(
                type=BlockingEventType.SESSION_DEAD,
                platform=self.name,
                detail="verified logged in but 0 listings on a normally-populated search (§B5)",
            )
        return await generic_detect_blocking_event(page, platform=self.name, listings_found=listings_found)


CollectFn = Callable[..., Awaitable[list[JobListing]]]
ApplyBatchFn = Callable[..., Awaitable[int]]
VerifyFn = Callable[[Page, str], Awaitable[bool]]
InjectCookiesFn = Callable[[BrowserContext, Path], Awaitable[None]]
UrlFn = Callable[["AppConfig"], str]


@dataclass(frozen=True)
class PlatformSpec:
    """Declarative, self-registering description of one job-board platform.

    Each site package (``naukri/``, ``hirist/``, ...) declares exactly one
    module-level ``PLATFORM = PlatformSpec(...)`` constant in its
    ``__init__.py``. ``discover_platforms()`` finds every such spec via
    filesystem scanning (no hardcoded platform-name list anywhere), and this
    module, ``browser.py``, and ``config.py`` all build their per-platform
    wiring (the adapter registry, the browser-session function, and the YAML
    ``filters:`` parsing) generically from the discovered specs.

    This is a *declarative wrapper* around functions/constants that already
    exist in each site package — it does not reimplement or rewrite any
    Playwright DOM logic; it only names the seams so the wiring layer can be
    generated instead of hand-maintained.
    """

    # --- identity ---
    name: str  # registry key / CLI --platform value, e.g. "naukri"
    platform_label: str  # human-readable label used in log/console messages, e.g. "Naukri"

    # --- auth wiring (matches browser.py's two existing paths) ---
    # "browser" = reuse a real Chrome profile (Google-OAuth-only sites like
    #             Wellfound/Uplers, where a headless ephemeral context can't
    #             complete the OAuth flow); "cookies" = ephemeral Playwright
    #             context + injected cookies.json (safe to run one per platform
    #             in parallel, unlike the shared Chrome profile).
    auth_method: str
    origin: UrlFn  # AppConfig -> origin URL. A function (not a plain str) because
    # Indeed's origin depends on the user's configured country domain
    # (config.platforms["indeed"].filters.domain) — every other platform's
    # origin function just ignores its `config` argument and returns a constant.
    login_url: UrlFn
    default_cookies_file: str
    # Plain (context, cookies_path) / (page, expected_name) callables — this is
    # exactly what the PlatformAdapter registry needs (matches this project's
    # pre-refactor behavior, where the adapter registry also used the plain,
    # unbound functions rather than a config-bound variant).
    inject_cookies: InjectCookiesFn
    verify_logged_in: VerifyFn

    # --- adapter wiring (see PlatformAdapter / _BaseAdapter above) ---
    collect_listings: CollectFn
    apply_batch: ApplyBatchFn

    # --- config wiring (Tier 1 step 6: config.py parses each platform's
    # `filters:` YAML block generically using these instead of a hardcoded
    # per-platform if/elif chain) ---
    filters_cls: type
    parse_filters: Callable[[dict], Any]

    # browser.py's generic session builder needs a *config-bound* variant of
    # inject_cookies/verify_logged_in for platforms whose auth depends on
    # runtime config (Indeed's country domain). None (the default, used by
    # every platform except Indeed) means "just use the plain
    # `inject_cookies`/`verify_logged_in` above, ignoring config".
    inject_cookies_bind: Callable[[AppConfig], InjectCookiesFn] | None = None
    verify_logged_in_bind: Callable[[AppConfig], VerifyFn] | None = None

    # Platforms whose adapter needs custom behavior (Indeed's live config read
    # for easy_apply_only/domain; Wellfound's §B5 blocking-event refinement)
    # supply their own _BaseAdapter subclass here instead of overriding a
    # detect_blocking_event field with something less flexible — a full adapter
    # subclass can override *any* of collect_listings/apply/verify_session/
    # detect_blocking_event, which a single-method override field couldn't.
    adapter_class: type = _BaseAdapter

    filters_enabled_default: bool = True

    # --- browser.py wiring ---
    # True only for "cookies"-auth platforms safe to run concurrently, each in
    # its own Chromium instance (browser.py's PARALLEL_COOKIE_PLATFORMS). The
    # two "browser"-auth (Chrome-profile) platforms share ONE real Chrome
    # profile directory, so running them concurrently would corrupt it —
    # parallel_safe must be False whenever auth_method == "browser".
    parallel_safe: bool = False


def discover_platforms() -> dict[str, PlatformSpec]:
    """Scan jobs_auto_apply's immediate subpackages for a module-level ``PLATFORM``.

    Any subpackage that doesn't define ``PLATFORM: PlatformSpec`` (``answers/``,
    ``ats/``, ``tailoring/``, ``resilience/``, ``profile/``, ...) is silently
    skipped — that's the expected, common case, not an error. A subpackage that
    fails to *import* (a real bug in that package) is logged and skipped rather
    than crashing every other platform's discovery, since one broken package
    shouldn't take the whole registry down.
    """
    import jobs_auto_apply as _pkg

    specs: dict[str, PlatformSpec] = {}
    prefix = f"{_pkg.__name__}."
    for module_info in pkgutil.iter_modules(_pkg.__path__, prefix):
        if not module_info.ispkg:
            continue
        try:
            module = importlib.import_module(module_info.name)
        except Exception:
            logger.exception("Platform discovery: failed to import subpackage %s — skipping", module_info.name)
            continue
        spec = getattr(module, "PLATFORM", None)
        if spec is None:
            continue
        if not isinstance(spec, PlatformSpec):
            logger.warning(
                "Platform discovery: %s.PLATFORM is not a PlatformSpec (%r) — skipping",
                module_info.name,
                type(spec),
            )
            continue
        specs[spec.name] = spec
    return specs


def _build_registry() -> dict[str, PlatformAdapter]:
    registry: dict[str, PlatformAdapter] = {}
    for spec in discover_platforms().values():
        adapter_cls = spec.adapter_class or _BaseAdapter
        registry[spec.name] = adapter_cls(spec.name, spec.collect_listings, spec.apply_batch, spec.verify_logged_in)
    return registry


ADAPTERS: dict[str, PlatformAdapter] = {}

# Judgment call (fix for a bug found in code review, not present in any shipping
# entry point but a real latent trap): ADAPTERS used to be populated eagerly by a
# bare _init() call at module-import time. Every <platform>/__init__.py imports
# this module as its first project import (to get PlatformSpec) and then, later
# in the same file, sets `PLATFORM = PlatformSpec(...)`. If something imports a
# platform package BEFORE this module (e.g. `import jobs_auto_apply.wellfound`
# as a script's first project import, rather than going through main.py/cli.py/
# browser.py, which all reach this module first today), discover_platforms()'s
# pkgutil scan re-enters that platform's module via sys.modules mid-import — its
# `PLATFORM` attribute doesn't exist yet — and getattr(module, "PLATFORM", None)
# silently returns None, dropping that one platform from the registry. Making
# initialization lazy (deferred to first real use via get_adapter(), rather than
# import time) sidesteps this: by the time anything actually calls get_adapter(),
# every platform package involved in the current process has long since finished
# importing, regardless of which module happened to trigger platform_adapter's
# own import first.
_initialized = False


def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    try:
        ADAPTERS.update(_build_registry())
        assert_adapters_valid()
    except Exception:
        logger.exception("Failed to initialize platform adapter registry")
        raise
    _initialized = True


def get_adapter(platform: str) -> PlatformAdapter | None:
    _ensure_initialized()
    return ADAPTERS.get(platform)


def register_adapter(name: str, adapter: PlatformAdapter) -> None:
    ADAPTERS[name] = adapter


def assert_adapters_valid() -> None:
    """Fail fast at startup (not mid-run) if an adapter doesn't satisfy the Protocol."""
    for name, adapter in ADAPTERS.items():
        if not isinstance(adapter, PlatformAdapter):
            raise RuntimeError(
                f"Platform adapter '{name}' does not satisfy the PlatformAdapter protocol "
                f"(missing collect_listings/apply/verify_session/detect_blocking_event)."
            )
# No eager _init() call here anymore — initialization is lazy, triggered by the
# first get_adapter() call (see _ensure_initialized's docstring above for why).
