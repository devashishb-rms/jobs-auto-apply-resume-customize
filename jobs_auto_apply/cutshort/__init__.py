"""Cutshort platform package — declares the self-registering PlatformSpec that
jobs_auto_apply.platform_adapter.discover_platforms() picks up automatically.
"""

from __future__ import annotations

from ..platform_adapter import PlatformSpec
from .apply import apply_batch
from .auth import CUTSHORT_LOGIN, CUTSHORT_ORIGIN, inject_cookies, verify_logged_in
from .config import DEFAULT_COOKIES_FILE, ENABLED_DEFAULT, CutshortFiltersConfig, parse_filters
from .search import collect_job_listings

PLATFORM = PlatformSpec(
    name="cutshort",
    platform_label="Cutshort",
    auth_method="cookies",
    origin=lambda config: CUTSHORT_ORIGIN,
    login_url=lambda config: CUTSHORT_LOGIN,
    default_cookies_file=DEFAULT_COOKIES_FILE,
    inject_cookies=inject_cookies,
    verify_logged_in=verify_logged_in,
    collect_listings=collect_job_listings,
    apply_batch=apply_batch,
    filters_cls=CutshortFiltersConfig,
    parse_filters=parse_filters,
    filters_enabled_default=ENABLED_DEFAULT,
    parallel_safe=True,
)
