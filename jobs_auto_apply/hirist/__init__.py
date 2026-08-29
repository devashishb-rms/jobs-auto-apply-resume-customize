"""Hirist platform package — declares the self-registering PlatformSpec that
jobs_auto_apply.platform_adapter.discover_platforms() picks up automatically.
"""

from __future__ import annotations

from ..platform_adapter import PlatformSpec
from .apply import apply_batch
from .auth import HIRIST_LOGIN, HIRIST_ORIGIN, inject_cookies, verify_logged_in
from .config import DEFAULT_COOKIES_FILE, ENABLED_DEFAULT, HiristFiltersConfig, parse_filters
from .search import collect_job_listings

PLATFORM = PlatformSpec(
    name="hirist",
    platform_label="Hirist",
    auth_method="cookies",
    origin=lambda config: HIRIST_ORIGIN,
    login_url=lambda config: HIRIST_LOGIN,
    default_cookies_file=DEFAULT_COOKIES_FILE,
    inject_cookies=inject_cookies,
    verify_logged_in=verify_logged_in,
    collect_listings=collect_job_listings,
    apply_batch=apply_batch,
    filters_cls=HiristFiltersConfig,
    parse_filters=parse_filters,
    filters_enabled_default=ENABLED_DEFAULT,
    parallel_safe=True,
)
