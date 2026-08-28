from __future__ import annotations

import re
from typing import Any

from ..config import AppConfig
from ..profile_data import ResumeFacts, build_resume_context, load_resume_facts
from .application_facts import load_application_facts


def _norm_skill(skill: str) -> str:
    text = skill.strip().lower().replace("-", " ").replace("/", " ")
    text = re.sub(r"[^a-z0-9.+#\s_]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(" ", "_")


def load_skill_context(config: AppConfig) -> tuple[ResumeFacts | None, dict[str, Any]]:
    app_facts = load_application_facts(config)
    try:
        facts = load_resume_facts(config.base_dir)
    except FileNotFoundError:
        facts = None
    return facts, app_facts


def skill_years_override(app_facts: dict[str, Any], skill: str) -> float | None:
    """Return explicit years from application_facts.skill_years, or None if unset."""
    raw = app_facts.get("skill_years") or {}
    if not isinstance(raw, dict):
        return None
    key = _norm_skill(skill)
    aliases = {
        key,
        key.replace("_", ""),
        skill.strip().lower(),
        skill.strip().lower().replace(" ", "_"),
        skill.strip().lower().replace(" ", ""),
    }
    for alias in aliases:
        if alias in raw:
            try:
                return float(raw[alias])
            except (TypeError, ValueError):
                return None
    # Loose match on keys.
    for k, v in raw.items():
        if _norm_skill(str(k)) == key:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def resume_blob(facts: ResumeFacts | None, config: AppConfig | None = None) -> str:
    parts: list[str] = []
    if facts is not None:
        parts.append(build_resume_context(facts).lower())
    if config is not None:
        skills = " ".join(config.profile.core_skills).lower()
        roles = " ".join(config.profile.target_roles).lower()
        parts.append(skills)
        parts.append(roles)
        parts.append(config.profile.headline.lower())
    return "\n".join(p for p in parts if p)


def has_skill(
    facts: ResumeFacts | None,
    skill: str,
    config: AppConfig | None = None,
    app_facts: dict[str, Any] | None = None,
) -> bool:
    override = skill_years_override(app_facts or {}, skill)
    if override is not None:
        return override > 0
    needle = skill.strip().lower()
    if not needle:
        return False
    if config is not None:
        for s in config.profile.core_skills:
            if needle in s.lower() or s.lower() in needle:
                return True
    blob = resume_blob(facts, config)
    tokens = [t for t in re.split(r"[\s_/.-]+", needle) if len(t) >= 2]
    if not tokens:
        return needle in blob
    return all(tok in blob for tok in tokens)


def skill_experience_configured(
    config: AppConfig,
    facts: ResumeFacts | None,
    app_facts: dict[str, Any],
    skill: str,
) -> bool:
    """True when skill_years declares the skill, or resume/core_skills mention it."""
    if skill_years_override(app_facts, skill) is not None:
        return True
    return has_skill(facts, skill, config, app_facts)


def skill_years_answer(
    config: AppConfig,
    facts: ResumeFacts | None,
    app_facts: dict[str, Any],
    skill: str,
) -> str | None:
    override = skill_years_override(app_facts, skill)
    if override is not None:
        if override <= 0:
            return "0"
        if override == int(override):
            return str(int(override))
        return str(override)
    if not has_skill(facts, skill, config, app_facts):
        return None
    years = int(getattr(config.profile, "years_experience", 0) or 0)
    if years <= 0:
        return None
    return str(years)
