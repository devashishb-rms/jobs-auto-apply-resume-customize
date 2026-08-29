"""Hirist's own filters config — see wellfound/config.py's module docstring for why
this now lives in the platform's own package instead of the monolithic config.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dataclasses_jsonschema import JsonSchemaMixin

DEFAULT_COOKIES_FILE = "cookies.hirist.json"
ENABLED_DEFAULT = True


@dataclass
class HiristFiltersConfig(JsonSchemaMixin):
    search_urls: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)  # set your role/skill keywords in config.yaml
    cities: list[str] = field(default_factory=list)  # empty = all India
    experience: str = "2-5"  # 0-2, 2-5, 5-10, 10+ when using keyword search UI
    experience_min: int | None = None  # minexp query param when building URLs
    experience_max: int | None = None  # maxexp query param when building URLs
    max_pages: int = 1  # SRP pages per search feed (apply page 1, then page 2, …)


def parse_filters(data: dict[str, Any]) -> HiristFiltersConfig:
    kw = data.get("keywords", [])
    urls = data.get("search_urls", [])
    cities = data.get("cities")
    return HiristFiltersConfig(
        search_urls=list(urls) if isinstance(urls, list) else [str(urls)] if urls else [],
        keywords=list(kw) if isinstance(kw, list) else [str(kw)] if kw else [],
        cities=list(cities) if isinstance(cities, list) else [],
        experience=str(data.get("experience", "2-5")),
        experience_min=data.get("experience_min"),
        experience_max=data.get("experience_max"),
        max_pages=int(data.get("max_pages", 1)),
    )
