"""Shared dataclasses for the tailoring subsystem (SRP: types only, no logic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExperienceEntry:
    company: str
    title: str
    period: str
    location: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class TailoredResume:
    name: str
    headline: str
    contact_line: str
    professional_summary: str
    key_skills: list[str]
    experience: list[ExperienceEntry]
    education: str


@dataclass
class TailoredResumeResult:
    pdf_path: Path | None
    variant_id: str | None
    ats_keyword_coverage: float
    tier: str  # "reuse" | "tier1" | "tier2" | "degraded"
    degraded: bool = False
    issues: list[str] = field(default_factory=list)
