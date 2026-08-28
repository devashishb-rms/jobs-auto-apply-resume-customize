"""StructuredResume -> ATS-safe PDF (SDA §A4.3, §A11).

SRP: rendering only. No Playwright/browser dependency — résumé rendering is
decoupled from the automation layer (§A3). Single-column layout, standard
section headers, no tables/text-boxes/multi-column skill grids — the
documented ATS parser failure modes for Workday/Greenhouse/Taleo-class
parsers.

Dependency choice (documented per the implementation prompt's requirement):
**reportlab**, not WeasyPrint. WeasyPrint needs system-level Cairo/Pango
libraries that aren't guaranteed present on every deployment target this
project already supports (Mac dev / Docker / AWS VM — see Annexure §B6's
"a fresh install must actually succeed" lesson); reportlab is pure Python,
installs from a wheel everywhere, and is more than sufficient for a
single-column, no-graphics résumé layout. Added to requirements.txt and
pyproject.toml in this same commit.

Page-count enforcement (§A4.3) measures the *actual rendered* PDF via pypdf
(already a project dependency — no new one needed for this half) rather than
estimating from character counts, so "exactly 2 pages" is verified against
real output, not a heuristic.
"""

from __future__ import annotations

import logging
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .models import TailoredResume

logger = logging.getLogger("job_apply")

_STYLES = getSampleStyleSheet()
_NAME_STYLE = ParagraphStyle("Name", parent=_STYLES["Title"], fontSize=16, spaceAfter=2, alignment=0)
_CONTACT_STYLE = ParagraphStyle("Contact", parent=_STYLES["Normal"], fontSize=9, spaceAfter=10)
_HEADER_STYLE = ParagraphStyle(
    "SectionHeader",
    parent=_STYLES["Heading2"],
    fontSize=12,
    spaceBefore=10,
    spaceAfter=4,
    textColor="#1a1a1a",
)
_BODY_STYLE = ParagraphStyle("Body", parent=_STYLES["Normal"], fontSize=11, leading=14, spaceAfter=4)
_BULLET_STYLE = ParagraphStyle(
    "Bullet", parent=_BODY_STYLE, leftIndent=14, bulletIndent=4, spaceAfter=2
)
_ROLE_STYLE = ParagraphStyle("Role", parent=_BODY_STYLE, fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=1)


def _build_flowables(resume: TailoredResume):
    flow = [
        Paragraph(resume.name, _NAME_STYLE),
        Paragraph(resume.headline, _CONTACT_STYLE),
        Paragraph(resume.contact_line, _CONTACT_STYLE),
    ]
    if resume.professional_summary.strip():
        flow.append(Paragraph("Summary", _HEADER_STYLE))
        flow.append(Paragraph(resume.professional_summary.strip(), _BODY_STYLE))

    if resume.key_skills:
        flow.append(Paragraph("Skills", _HEADER_STYLE))
        flow.append(Paragraph(", ".join(resume.key_skills), _BODY_STYLE))

    if resume.experience:
        flow.append(Paragraph("Experience", _HEADER_STYLE))
        for role in resume.experience:
            line = f"<b>{role.title}</b> — {role.company}"
            if role.period:
                line += f" ({role.period})"
            flow.append(Paragraph(line, _ROLE_STYLE))
            for bullet in role.bullets:
                flow.append(Paragraph(f"&bull; {bullet}", _BULLET_STYLE))

    if resume.education.strip():
        flow.append(Paragraph("Education", _HEADER_STYLE))
        flow.append(Paragraph(resume.education.strip(), _BODY_STYLE))

    return flow


def render_to_pdf(resume: TailoredResume, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        title=f"{resume.name} - Resume",
    )
    doc.build(_build_flowables(resume))


def page_count(pdf_path: Path) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        logger.warning("render.page_count failed for %s", pdf_path, exc_info=True)
        return -1


def _bullet_relevance_score(bullet: str, keywords: list[str]) -> int:
    low = bullet.lower()
    return sum(1 for kw in keywords if kw.lower() in low)


def trim_lowest_relevance_bullet(resume: TailoredResume, keywords: list[str]) -> bool:
    """§A4.3: if content overflows 2 pages, trim the lowest-JD-relevance bullet
    first. Returns True if a bullet was actually removed (False = nothing left
    to trim, caller should give up and fall back)."""
    candidates: list[tuple[int, int, int]] = []  # (score, role_idx, bullet_idx)
    for ri, role in enumerate(resume.experience):
        for bi, bullet in enumerate(role.bullets):
            candidates.append((_bullet_relevance_score(bullet, keywords), ri, bi))
    if not candidates:
        return False
    candidates.sort(key=lambda c: c[0])
    _, ri, bi = candidates[0]
    if len(resume.experience[ri].bullets) <= 1:
        # Don't remove the last bullet from a role — that role becomes bare;
        # try the next-lowest-relevance candidate that isn't a role's last bullet.
        remaining = [c for c in candidates if len(resume.experience[c[1]].bullets) > 1]
        if not remaining:
            return False
        _, ri, bi = remaining[0]
    del resume.experience[ri].bullets[bi]
    return True


def render_within_page_budget(
    resume: TailoredResume,
    out_path: Path,
    *,
    keywords: list[str],
    max_pages: int = 2,
    max_repair_loops: int = 2,
) -> tuple[bool, int]:
    """Render, then trim lowest-relevance bullets until within max_pages or the
    repair-loop budget is exhausted. Returns (within_budget, final_page_count)."""
    render_to_pdf(resume, out_path)
    pages = page_count(out_path)
    loops = 0
    while pages > max_pages and loops < max_repair_loops:
        if not trim_lowest_relevance_bullet(resume, keywords):
            break
        render_to_pdf(resume, out_path)
        pages = page_count(out_path)
        loops += 1
    return pages <= max_pages, pages
