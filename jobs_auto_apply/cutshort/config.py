"""Cutshort's own filters config — see wellfound/config.py's module docstring for why
this now lives in the platform's own package instead of the monolithic config.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dataclasses_jsonschema import JsonSchemaMixin

DEFAULT_COOKIES_FILE = "cookies.cutshort.json"
ENABLED_DEFAULT = True


@dataclass
class CutshortFiltersConfig(JsonSchemaMixin):
    search_urls: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)  # e.g. ["backend developer", "python"]
    cities: list[str] = field(default_factory=list)  # empty = India-wide category pages
    max_pages: int = 1  # SRP pages per search feed


def parse_filters(data: dict[str, Any]) -> CutshortFiltersConfig:
    kw = data.get("keywords", [])
    urls = data.get("search_urls", [])
    cities = data.get("cities")
    return CutshortFiltersConfig(
        search_urls=list(urls) if isinstance(urls, list) else [str(urls)] if urls else [],
        keywords=list(kw) if isinstance(kw, list) else [str(kw)] if kw else [],
        cities=list(cities) if isinstance(cities, list) else [],
        max_pages=int(data.get("max_pages", 1)),
    )
