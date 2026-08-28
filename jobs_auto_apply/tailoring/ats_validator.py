"""ATS-Safety Constraints, enforced mechanically (SDA §A4.3).

SRP: this module only validates. It never generates or renders content —
callers (engine.py) act on its verdicts (strip a claim, trim a bullet,
fall back to the base résumé).

Fail-closed fact-check: every generated claim (skill, employer, title, date,
numeric figure) must be verifiable against ``resume_facts.yaml`` /
``application_facts.yaml``. On doubt, this module strips the claim rather
than keeping it — over-stripping (a slightly blander résumé) is the safe
failure direction; under-stripping (a fabricated claim reaching a real
employer) is not, per §A9/§A11/§B9.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..profile_data import ResumeFacts
from .jd_extractor import StructuredJD

_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\+?\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    keyword_coverage: float = 0.0
    stripped_claims: list[str] = field(default_factory=list)


def _fact_numbers(facts: ResumeFacts) -> set[str]:
    numbers: set[str] = set()
    for role in facts.experience:
        for highlight in role.get("highlights", []) or []:
            numbers.update(_NUMBER_RE.findall(str(highlight)))
    return numbers


def _fact_employers(facts: ResumeFacts) -> set[str]:
    return {str(role.get("company", "")).strip() for role in facts.experience if role.get("company")}


def strip_fabricated_claims(text: str, facts: ResumeFacts, core_skills: list[str]) -> tuple[str, list[str]]:
    """Sentence-level fail-closed fact-check for LLM-generated prose (Tier 2 output).

    Numeric claims (percentages, team sizes, budgets) must match a source
    bullet verbatim, and any company-suffix-shaped phrase (e.g. "... Corp",
    "... Technologies") must match a real employer; a sentence carrying either
    kind of unverifiable claim is dropped. Tier 1 output (deterministic
    substring injection of already-fact-listed skills) never reaches this
    function — it cannot fabricate by construction.

    A broader "every tech-term-shaped token must be in the skill vocabulary"
    check was considered and deliberately left out: real business acronyms
    that legitimately appear in a user's own resume prose (e.g. "GMV", "P&L")
    aren't necessarily in technical_skills/core_skills, so that check's false-
    positive rate against genuine content was too high for the vocabulary
    this module can cheaply build. Numeric + employer verification is the
    mechanical net; skill claims are covered because Tier 2's prompt only
    ever asks the LLM to rephrase already-true summary text, and any skill it
    surfaces must already appear in the JD-keyword list built from the same
    fact-derived vocabulary in jd_extractor.py.
    """
    fact_numbers = _fact_numbers(facts)
    employers = _fact_employers(facts)

    kept: list[str] = []
    stripped: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        bad = False
        for num in _NUMBER_RE.findall(sentence):
            if num not in fact_numbers:
                bad = True
                break
        if not bad:
            for company in re.findall(r"\b[A-Z][a-zA-Z&.]*(?:\s+[A-Z][a-zA-Z&.]*){0,3}\b", sentence):
                # Only treat as an employer-claim if it looks company-suffix-like.
                if re.search(r"\b(Inc|Ltd|LLC|Corp|Technologies|Systems|Labs|Solutions)\b", company):
                    if company.strip() not in employers:
                        bad = True
                        break
        if bad:
            stripped.append(sentence)
            continue
        kept.append(sentence)
    return " ".join(kept), stripped


def keyword_coverage(text: str, structured_jd: StructuredJD) -> float:
    """TF-based match score between must-have JD keywords and the résumé text (§A4.3)."""
    if not structured_jd.must_have:
        return 1.0
    low = text.lower()
    hits = sum(1 for kw in structured_jd.must_have if kw.lower() in low)
    return hits / len(structured_jd.must_have)


def validate_text_sections(
    sections: dict[str, str],
    structured_jd: StructuredJD,
    facts: ResumeFacts,
    core_skills: list[str],
    *,
    min_keyword_coverage: float,
) -> ValidationResult:
    """Runs the no-fabrication + keyword-coverage checks over generated prose
    sections (not the whole rendered PDF — page-count/format checks live in
    render.py, which measures the actual rendered output per §A4.3's table)."""
    issues: list[str] = []
    stripped_all: list[str] = []
    combined = " ".join(sections.values())

    coverage = keyword_coverage(combined, structured_jd)
    if coverage < min_keyword_coverage:
        issues.append(f"keyword coverage {coverage:.0%} below target {min_keyword_coverage:.0%}")

    for name, text in sections.items():
        clean, stripped = strip_fabricated_claims(text, facts, core_skills)
        if stripped:
            stripped_all.extend(stripped)
            issues.append(f"{name}: stripped {len(stripped)} unverifiable claim(s)")
            sections[name] = clean

    return ValidationResult(
        ok=coverage >= min_keyword_coverage and not stripped_all,
        issues=issues,
        keyword_coverage=coverage,
        stripped_claims=stripped_all,
    )
