"""Wellfound platform package — declares the self-registering PlatformSpec that
jobs_auto_apply.platform_adapter.discover_platforms() picks up automatically.

This is a declarative wrapper around functions that already exist in this
package (auth.py, search.py, apply.py) — no DOM logic lives here.
"""

from __future__ import annotations

from ..platform_adapter import PlatformSpec, _WellfoundAdapter
from .apply import apply_batch
from .auth import WELLFOUND_ORIGIN, inject_cookies, verify_logged_in
from .config import DEFAULT_COOKIES_FILE, ENABLED_DEFAULT, WellfoundFiltersConfig, parse_filters
from .search import collect_job_listings

PLATFORM = PlatformSpec(
    name="wellfound",
    platform_label="Wellfound",
    # Wellfound is Google-OAuth-only — an ephemeral headless context can't complete
    # that flow, so this reuses the user's real signed-in Chrome profile.
    auth_method="browser",
    origin=lambda config: WELLFOUND_ORIGIN,
    login_url=lambda config: f"{WELLFOUND_ORIGIN}/login",
    default_cookies_file=DEFAULT_COOKIES_FILE,
    inject_cookies=inject_cookies,
    verify_logged_in=verify_logged_in,
    collect_listings=collect_job_listings,
    apply_batch=apply_batch,
    filters_cls=WellfoundFiltersConfig,
    parse_filters=parse_filters,
    filters_enabled_default=ENABLED_DEFAULT,
    # Chrome-profile auth platforms share one real profile dir — never parallel-safe.
    parallel_safe=False,
    # §B5: headless Chromium without a real Chrome profile can verify "logged in"
    # while rendering a signup page instead of the real search — see
    # _WellfoundAdapter's docstring in platform_adapter.py.
    adapter_class=_WellfoundAdapter,
)
