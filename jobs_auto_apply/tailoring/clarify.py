"""Tier 3 — human clarification, ≤2 questions (SDA §A4.1 Tier 3).

SRP: this module only decides which requirements the fact base cannot
resolve, and notifies. It reuses ``pending_questions.py`` and the existing
Telegram plumbing (``telegram.send_notification``, added in Phase 2) rather
than building a second notification mechanism.

Scoping decision (documented — a judgment call the SDA leaves room for):
this implementation does **not** open a new synchronous wait loop inside the
apply pipeline for a Telegram reply. §A4.1 itself says the point of Tier 3 is
to avoid "stalling the apply pipeline indefinitely on one job" and specifies
a safe default (omit the unresolved claim) on timeout regardless of whether a
reply arrives. Given that the safe default applies unconditionally either
way, this implementation applies it immediately for the *current* résumé
(so this job's apply is never slowed down) and sends one batched, informational
Telegram notification listing what was omitted and why — v2's existing
pending-question/Telegram flow already lets the user answer at their own pace
for *future* jobs (once ``application_facts.yaml`` is updated, later
tailoring runs pick it up automatically). Building a second, tailoring-specific
synchronous wait-for-reply loop alongside the existing pending_questions
machinery would duplicate infrastructure for a path the SDA's own cost model
(§A4.4) shows is ~1% of applications — flagged explicitly as a possible
follow-up (a real bounded wait, reusing pending_questions' group/reply
routing) rather than silently scoped out.
"""

from __future__ import annotations

import logging
import re

from ..config import AppConfig
from ..profile_data import ResumeFacts
from ..telegram import send_notification
from ..utils import JobListing
from .jd_extractor import StructuredJD

logger = logging.getLogger("job_apply")

# Requirement phrases the fact base structurally cannot answer either way
# (nothing in resume_facts.yaml / application_facts.yaml covers them) — kept
# as a small, explicit static list rather than an LLM call, since these are
# rare, high-stakes, and better handled deterministically.
_UNRESOLVABLE_PATTERNS: dict[str, re.Pattern] = {
    "active security clearance": re.compile(r"security\s*clearance", re.I),
    "specific work visa / citizenship requirement": re.compile(
        r"\b(work\s*visa|citizenship\s*required|us\s*citizen|green\s*card)\b", re.I
    ),
    "professional license/certification not on file": re.compile(
        r"\b(licensed|licen[cs]e\s*required|certified\s*[A-Z]{2,6}\b)\b"
    ),
}


def find_unresolved_requirements(structured_jd: StructuredJD, jd_text: str, facts: ResumeFacts) -> list[str]:
    found: list[str] = []
    for label, pattern in _UNRESOLVABLE_PATTERNS.items():
        if pattern.search(jd_text):
            found.append(label)
    return found[:2]  # cap at 2 per résumé (§A4.1 Tier 3)


def notify_unresolved(config: AppConfig, job: JobListing, questions: list[str]) -> None:
    if not questions:
        return
    text = (
        f"[jobs-auto-apply] Résumé tailoring for '{job.title}' @ {job.company} found "
        f"{len(questions)} requirement(s) your facts don't cover (omitted from this résumé — "
        "safe default, nothing fabricated):\n" + "\n".join(f"- {q}" for q in questions)
        + "\n\nAdd these to profile/application_facts.yaml if you want future tailoring to use them."
    )
    send_notification(config, text)
    logger.info("Tailoring clarify: %d unresolved requirement(s) for %s @ %s", len(questions), job.title, job.company)
