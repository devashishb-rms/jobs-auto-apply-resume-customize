"""Instahyre's own filters config — see wellfound/config.py's module docstring for why
this now lives in the platform's own package instead of the monolithic config.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dataclasses_jsonschema import JsonSchemaMixin

DEFAULT_COOKIES_FILE = "cookies.instahyre.json"
ENABLED_DEFAULT = True


@dataclass
class InstahyreFiltersConfig(JsonSchemaMixin):
    search_urls: list[str] = field(default_factory=list)
    feeds: list[dict[str, Any]] = field(default_factory=list)
    # Job functions to target (human names like "Backend Development", or raw
    # Instahyre "/api/v1/job_function/<id>" paths). Empty = use the built-in
    # fallback in instahyre/feeds.py.
    job_functions: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    experience_years: int | None = None
    company_size: str = "All"  # Small, Medium, Large, All
    # --- Instahyre platform mappings (override/extend the built-in defaults so the
    # tool works for any role family, not just backend/software) ---
    # Fallback skills string used when a search feed doesn't specify its own skills.
    default_skills: str = ""
    # Map a human job-function name (case-insensitive) to its Instahyre API path,
    # e.g. {"data science": "/api/v1/job_function/12"}. Merged over the built-ins.
    job_function_aliases: dict[str, str] = field(default_factory=dict)
    # Map a skill keyword (lowercase) to the Instahyre selectize chip data-value,
    # e.g. {"node": "Nodejs", "golang": "Go"}. Merged over the built-ins.
    skill_chip_values: dict[str, str] = field(default_factory=dict)
    # Map a chip data-value to the text to type to surface it in the dropdown,
    # e.g. {"Nodejs": "nodejs"}. Merged over the built-ins.
    skill_type_queries: dict[str, str] = field(default_factory=dict)


def _str_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def parse_filters(data: dict[str, Any]) -> InstahyreFiltersConfig:
    urls = data.get("search_urls", [])
    raw_feeds = data.get("feeds", [])
    feeds = [f for f in raw_feeds if isinstance(f, dict)] if isinstance(raw_feeds, list) else []
    return InstahyreFiltersConfig(
        search_urls=list(urls) if isinstance(urls, list) else [str(urls)] if urls else [],
        feeds=feeds,
        job_functions=list(data.get("job_functions", [])),
        locations=list(data.get("locations", [])),
        experience_years=data.get("experience_years"),
        company_size=str(data.get("company_size", "All")),
        default_skills=str(data.get("default_skills", "")),
        job_function_aliases=_str_str_map(data.get("job_function_aliases")),
        skill_chip_values=_str_str_map(data.get("skill_chip_values")),
        skill_type_queries=_str_str_map(data.get("skill_type_queries")),
    )
