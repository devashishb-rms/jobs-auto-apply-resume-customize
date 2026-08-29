"""Indeed's own filters config — see wellfound/config.py's module docstring for why
this now lives in the platform's own package instead of the monolithic config.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dataclasses_jsonschema import JsonSchemaMixin

DEFAULT_COOKIES_FILE = "cookies.indeed.json"
ENABLED_DEFAULT = True


@dataclass
class IndeedFiltersConfig(JsonSchemaMixin):
    # Indeed serves country-specific subdomains (www.indeed.com for the US,
    # in.indeed.com for India, uk.indeed.com, etc). This tool is India-focused,
    # so the default is in.indeed.com — override for another country's site.
    domain: str = "in.indeed.com"
    search_urls: list[str] = field(default_factory=list)
    keywords: str = ""  # maps to Indeed's `q` search param — set your role/skills in config.yaml
    locations: list[str] = field(default_factory=list)  # maps to `l`; multiple = one feed per location
    max_job_age_days: int | None = None  # `fromage` query param (e.g. 7 → posted within last 7 days)
    sort: str = "date"  # date (newest first) | relevance
    # Restrict to native "Indeed Apply" listings only (skip "apply on company site" postings).
    # Detected client-side from the "Easily apply" badge on each job card — Indeed does not
    # expose a documented, stable URL filter code for this, so it isn't done via search_urls.
    easy_apply_only: bool = True
    max_pages: int = 1  # SRP pages per search feed (start=0,10,20,… — apply page 1, then page 2, …)


def parse_filters(data: dict[str, Any]) -> IndeedFiltersConfig:
    urls = data.get("search_urls", [])
    locations = data.get("locations")
    return IndeedFiltersConfig(
        domain=str(data.get("domain") or data.get("country") or "in.indeed.com"),
        search_urls=list(urls) if isinstance(urls, list) else [str(urls)] if urls else [],
        keywords=str(data.get("keywords", "")),
        locations=list(locations) if isinstance(locations, list) else [],
        max_job_age_days=data.get("max_job_age_days"),
        sort=str(data.get("sort", "date")),
        easy_apply_only=bool(data.get("easy_apply_only", True)),
        max_pages=int(data.get("max_pages", 1)),
    )
