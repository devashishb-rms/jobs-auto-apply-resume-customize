"""Wellfound's own filters config — moved out of the monolithic jobs_auto_apply/config.py
so this platform is fully self-contained (Tier 1 self-registering PlatformSpec refactor).

``AppConfig`` no longer has a hardcoded ``WellfoundFiltersConfig`` field; instead
``jobs_auto_apply/config.py``'s ``load_config()`` discovers this platform via
``jobs_auto_apply.platform_adapter.discover_platforms()`` and calls
``PLATFORM.parse_filters`` (== :func:`parse_filters` below) on the YAML
``wellfound.filters:`` block, storing the result in
``AppConfig.platforms["wellfound"].filters``. Every default value below is
copied byte-for-byte from the pre-refactor ``WellfoundFiltersConfig`` /
``_wellfound_filters`` in config.py so existing config.yaml files parse to
identical values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dataclasses_jsonschema import JsonSchemaMixin

DEFAULT_COOKIES_FILE = "cookies.wellfound.json"
ENABLED_DEFAULT = True


@dataclass
class WellfoundFiltersConfig(JsonSchemaMixin):
    use_profile_filters: bool = True
    roles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote_policy: str = "some"
    experience_levels: list[str] = field(default_factory=list)
    job_types: list[str] = field(default_factory=list)
    keywords: str = ""
    skills: list[str] = field(default_factory=list)
    salary_min: int | None = None
    salary_currency: str = "INR"
    include_no_salary: bool = True
    recently_active: str | None = "week"
    sort: str = "newest"


def parse_filters(data: dict[str, Any]) -> WellfoundFiltersConfig:
    return WellfoundFiltersConfig(
        use_profile_filters=bool(data.get("use_profile_filters", True)),
        roles=list(data.get("roles", [])),
        locations=list(data.get("locations", [])),
        remote_policy=str(data.get("remote_policy", "some")),
        experience_levels=list(data.get("experience_levels", [])),
        job_types=list(data.get("job_types", [])),
        keywords=str(data.get("keywords", "")),
        skills=list(data.get("skills", [])),
        salary_min=data.get("salary_min"),
        salary_currency=str(data.get("salary_currency", "INR")),
        include_no_salary=bool(data.get("include_no_salary", True)),
        recently_active=data.get("recently_active"),
        sort=str(data.get("sort", "newest")),
    )
