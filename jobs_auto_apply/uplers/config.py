"""Uplers' own filters config — see wellfound/config.py's module docstring for why
this now lives in the platform's own package instead of the monolithic config.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dataclasses_jsonschema import JsonSchemaMixin

DEFAULT_COOKIES_FILE = "cookies.uplers.json"
ENABLED_DEFAULT = True


@dataclass
class UplersFiltersConfig(JsonSchemaMixin):
    keywords: str = ""
    skills: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    experience_years_min: int | None = 3
    remote_only: bool = False


def parse_filters(data: dict[str, Any]) -> UplersFiltersConfig:
    return UplersFiltersConfig(
        keywords=str(data.get("keywords", "")),
        skills=list(data.get("skills", [])),
        locations=list(data.get("locations", [])),
        roles=list(data.get("roles", [])),
        experience_years_min=data.get("experience_years_min", 3),
        remote_only=bool(data.get("remote_only", False)),
    )
