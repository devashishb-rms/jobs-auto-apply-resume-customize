"""Formal Platform Adapter contract (v3 §A6, §B4).

Every existing v2 site package (``naukri/``, ``hirist/``, ``wellfound/``,
``instahyre/``, ``cutshort/``, ``uplers/``) already exposes ``apply_batch`` /
``collect_job_listings`` functions that ``cli.py`` calls by name via hand-maintained
dicts. This module formalizes that implicit contract as a ``runtime_checkable``
``Protocol`` and wraps each site's existing functions in a thin adapter object —
**no Playwright DOM logic is rewritten here**, only the seams are named.

This is a pure, additive refactor: nothing in this module changes what any
existing CLI command does. ``cli.py`` keeps calling the site packages' functions
directly (unchanged); the resilience engine (Phase 2) and the tailoring shadow
hook (Phase 3) are what actually read from ``ADAPTERS``.

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

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from playwright.async_api import BrowserContext, Page

from .config import AppConfig
from .utils import JobListing

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


def _build_registry() -> dict[str, PlatformAdapter]:
    from .cutshort.apply import apply_batch as cutshort_apply_batch
    from .cutshort.auth import verify_logged_in as cutshort_verify
    from .cutshort.search import collect_job_listings as cutshort_collect
    from .hirist.apply import apply_batch as hirist_apply_batch
    from .hirist.auth import verify_logged_in as hirist_verify
    from .hirist.search import collect_job_listings as hirist_collect
    from .instahyre.apply import apply_batch as instahyre_apply_batch
    from .instahyre.auth import verify_logged_in as instahyre_verify
    from .instahyre.search import collect_job_listings as instahyre_collect
    from .naukri.apply import apply_batch as naukri_apply_batch
    from .naukri.auth import verify_logged_in as naukri_verify
    from .naukri.search import collect_job_listings as naukri_collect
    from .uplers.apply import apply_batch as uplers_apply_batch
    from .uplers.auth import verify_logged_in as uplers_verify
    from .uplers.search import collect_job_listings as uplers_collect
    from .wellfound.apply import apply_batch as wellfound_apply_batch
    from .wellfound.auth import verify_logged_in as wellfound_verify
    from .wellfound.search import collect_job_listings as wellfound_collect

    return {
        "naukri": _BaseAdapter("naukri", naukri_collect, naukri_apply_batch, naukri_verify),
        "hirist": _BaseAdapter("hirist", hirist_collect, hirist_apply_batch, hirist_verify),
        "instahyre": _BaseAdapter("instahyre", instahyre_collect, instahyre_apply_batch, instahyre_verify),
        "cutshort": _BaseAdapter("cutshort", cutshort_collect, cutshort_apply_batch, cutshort_verify),
        "uplers": _BaseAdapter("uplers", uplers_collect, uplers_apply_batch, uplers_verify),
        "wellfound": _WellfoundAdapter("wellfound", wellfound_collect, wellfound_apply_batch, wellfound_verify),
    }


ADAPTERS: dict[str, PlatformAdapter] = {}


def get_adapter(platform: str) -> PlatformAdapter | None:
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


def _init() -> None:
    try:
        ADAPTERS.update(_build_registry())
        assert_adapters_valid()
    except Exception:
        logger.exception("Failed to initialize platform adapter registry")
        raise


_init()
