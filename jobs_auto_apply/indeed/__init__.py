"""Indeed platform package — declares the self-registering PlatformSpec that
jobs_auto_apply.platform_adapter.discover_platforms() picks up automatically.

Indeed is the one platform whose origin/login URL and cookie domain depend on
runtime config (the user's configured country subdomain, e.g. in.indeed.com vs
www.indeed.com) rather than being fixed constants — see indeed/auth.py's module
docstring. ``origin``/``login_url`` are plain functions of ``AppConfig`` (every
platform's spec has these, so this isn't special-cased); ``inject_cookies_bind``/
``verify_logged_in_bind`` bind the domain the same way browser.py's pre-refactor
``indeed_session()`` did with ``functools.partial``.
"""

from __future__ import annotations

import functools

from ..platform_adapter import PlatformSpec, _IndeedAdapter
from .apply import apply_batch
from .auth import indeed_login_url, indeed_origin, inject_cookies, verify_logged_in
from .config import DEFAULT_COOKIES_FILE, ENABLED_DEFAULT, IndeedFiltersConfig, parse_filters
from .search import collect_job_listings


def _domain(config) -> str | None:
    return getattr(config.platforms["indeed"].filters, "domain", None)


PLATFORM = PlatformSpec(
    name="indeed",
    platform_label="Indeed",
    # Indeed supports plain email/password sign-in (unlike Wellfound/Uplers'
    # Google-OAuth-only flow), so it follows Hirist's cookie-based pattern.
    auth_method="cookies",
    origin=lambda config: indeed_origin(_domain(config)),
    login_url=lambda config: indeed_login_url(_domain(config)),
    default_cookies_file=DEFAULT_COOKIES_FILE,
    inject_cookies=inject_cookies,
    verify_logged_in=verify_logged_in,
    inject_cookies_bind=lambda config: functools.partial(inject_cookies, domain=_domain(config)),
    verify_logged_in_bind=lambda config: functools.partial(verify_logged_in, domain=_domain(config)),
    collect_listings=collect_job_listings,
    apply_batch=apply_batch,
    filters_cls=IndeedFiltersConfig,
    parse_filters=parse_filters,
    filters_enabled_default=ENABLED_DEFAULT,
    parallel_safe=True,
    # §Issue 8: reads config.platforms["indeed"].filters live at collect-listings
    # call time (easy_apply_only/domain) instead of frozen registry-build-time
    # kwargs — see _IndeedAdapter's docstring in platform_adapter.py.
    adapter_class=_IndeedAdapter,
)
