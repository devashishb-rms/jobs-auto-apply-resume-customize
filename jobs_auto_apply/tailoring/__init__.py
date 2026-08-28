"""Resume Tailoring Subsystem — public surface (SDA §A4, §A8).

``tailor_for_job`` is the one thing ``apply_runner.py`` calls. Everything else
in this package (jd_extractor, variant_store, engine, ats_validator, render,
clarify) is an implementation detail reached only through ``engine.py``.

No effect at all unless ``application.resume_tailoring_enabled`` is true —
importing this package is safe and cheap regardless (no module-level I/O or
model loading happens on import; models/embeddings load lazily on first use).
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime, timezone

from ..config import AppConfig
from ..resume_context import use_resume_path
from ..utils import JobListing
from .engine import get_or_tailor_resume
from .models import TailoredResumeResult

logger = logging.getLogger("job_apply")


def _shadow_log_path(config: AppConfig):
    return config.resume_variants_dir / "shadow_log.jsonl"


def _log_shadow_event(config: AppConfig, job: JobListing, result: TailoredResumeResult, *, shadow: bool) -> None:
    """Append one line per tailored résumé so shadow-mode output is reviewable
    (SDA §A13 Phase 2: "logs what would have been uploaded and its
    ATS-validation result")."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "job_source": job.source,
        "job_id": job.job_id,
        "job_title": job.title,
        "job_company": job.company,
        "shadow_mode": shadow,
        "tier": result.tier,
        "variant_id": result.variant_id,
        "pdf_path": str(result.pdf_path) if result.pdf_path else None,
        "ats_keyword_coverage": result.ats_keyword_coverage,
        "would_upload": (not shadow) and result.pdf_path is not None,
        "issues": result.issues,
    }
    try:
        path = _shadow_log_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("tailoring shadow log write failed (non-fatal)", exc_info=True)
    logger.info(
        "Tailoring[%s]: tier=%s coverage=%.0f%% shadow=%s job=%s @ %s",
        "shadow" if shadow else "live",
        result.tier,
        result.ats_keyword_coverage * 100,
        shadow,
        job.title,
        job.company,
    )


@contextlib.asynccontextmanager
async def tailor_for_job(job: JobListing, config: AppConfig):
    """Async context manager wrapping a single job's apply call.

    - Flag off: no-op (zero import/behavior cost beyond this check).
    - Flag on + shadow mode (default when enabled): generates/validates a
      tailored résumé, logs what *would* have been uploaded, but the caller's
      apply still uses the static resume.pdf (no resume_context override is
      set).
    - Flag on + shadow mode off: sets the resume_context override for the
      duration of the apply call, so the site adapter's upload step (which
      always reads config.resume_path) picks up the tailored PDF instead.
    - Any failure anywhere in the pipeline falls back silently to the static
      résumé and is logged as tailoring_degraded — an apply is never blocked
      on a tailoring failure.
    """
    if not config.application.resume_tailoring_enabled:
        yield
        return

    # Tailoring involves CPU-bound embedding + local LLM calls; running it
    # synchronously on the event loop briefly blocks other concurrent apply
    # workers. Offloading to a thread keeps this additive change from
    # regressing parallel-apply throughput.
    import asyncio

    result = await asyncio.to_thread(get_or_tailor_resume, job, config)

    if result.degraded or result.pdf_path is None:
        # Nothing to log beyond what engine.py already logged; fall back silently.
        yield
        return

    shadow = config.application.resume_tailoring_shadow_mode
    _log_shadow_event(config, job, result, shadow=shadow)

    if shadow:
        yield
        return

    with use_resume_path(result.pdf_path):
        yield


__all__ = ["tailor_for_job", "get_or_tailor_resume"]
