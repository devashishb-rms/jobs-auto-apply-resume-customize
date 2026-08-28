from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Frame, Page

from ..resume_upload import attach_resume

logger = logging.getLogger("job_apply")

Surface = Page | Frame


async def attach_resume_on_apply_step(page: Page, resume_path: Path, *, scope: Surface | None = None) -> bool:
    """Attach the local résumé on Indeed Apply's résumé step, if one is present.

    Indeed's own résumé step usually offers to reuse a résumé already stored on
    the candidate's Indeed profile (no upload needed on repeat applications) and
    only shows a raw file-upload control the first time, or when "Upload a
    different resume" is chosen. This only attempts the generic file-input /
    upload-trigger attach (via the shared ``resume_upload`` helper every other
    site package uses) — it does not try to click "use existing resume" text,
    since that flow could not be observed without a real login. If Indeed
    already has a résumé on file and shows no upload control, this simply
    returns False and the apply flow proceeds (Indeed reuses the profile résumé
    by default).
    """
    if not resume_path.exists():
        logger.warning("Resume not found at %s — skipping Indeed résumé attach", resume_path)
        return False
    return await attach_resume(page, resume_path, scope=scope)
