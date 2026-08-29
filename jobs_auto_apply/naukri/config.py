"""Naukri's own filters config — see wellfound/config.py's module docstring for why
this now lives in the platform's own package instead of the monolithic config.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dataclasses_jsonschema import JsonSchemaMixin

DEFAULT_COOKIES_FILE = "cookies.naukri.json"
ENABLED_DEFAULT = True


@dataclass
class NaukriFiltersConfig(JsonSchemaMixin):
    keywords: str = ""  # search keywords; set to your role/skills in config.yaml
    locations: list[str] = field(default_factory=list)  # empty = India-wide (no city in URL)
    experience_min: int | None = 3
    experience_max: int | None = 8
    salary_min_lakhs: float | None = None
    remote_only: bool = False
    quick_apply_only: bool = True
    sort: str = "freshness"  # freshness | date | newest | relevance
    max_job_age_days: int | None = None  # e.g. 7 → "Last 7 days" in Freshness filter
    max_pages: int = 1  # scroll batches on single Aurus SRP (scroll → collect → apply)


def parse_filters(data: dict[str, Any]) -> NaukriFiltersConfig:
    loc = data.get("locations")
    return NaukriFiltersConfig(
        keywords=str(data.get("keywords", "")),
        locations=list(loc) if isinstance(loc, list) else [],
        experience_min=data.get("experience_min", 3),
        experience_max=data.get("experience_max"),
        salary_min_lakhs=data.get("salary_min_lakhs"),
        remote_only=bool(data.get("remote_only", False)),
        quick_apply_only=bool(data.get("quick_apply_only", True)),
        sort=str(data.get("sort", "freshness")),
        max_job_age_days=data.get("max_job_age_days"),
        max_pages=int(data.get("max_pages", 1)),
    )
