from __future__ import annotations

import contextlib
import logging
import re

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..application_questions import resolve_question_answers
from ..apply_runner import run_apply_batch
from ..config import AppConfig
from ..page_load import prepare_interactive_page
from ..pending_questions import queue_unanswered
from ..resume_upload import upload_resume
from ..utils import JobListing, defer_job_for_run, job_key, save_applied_job
from .questions import (
    click_cutshort_advance,
    discover_cutshort_questions,
    fill_cutshort_questions,
)

logger = logging.getLogger("job_apply")

_APPLY_PATTERNS = (
    r"^apply to this job$",
    r"^apply now$",
    r"^apply$",
    r"^easy apply$",
)


async def _page_jd(page: Page) -> str:
    for sel in ("main", "article", '[class*="description" i]', "body"):
        loc = page.locator(sel)
        if await loc.count() > 0:
            text = (await loc.first.inner_text()).strip()
            if len(text) > 120:
                return text[:12000]
    return ""


async def _already_applied(page: Page) -> bool:
    body = (await page.locator("body").inner_text()).lower()
    if any(
        phrase in body
        for phrase in (
            "you have already applied",
            "already applied",
            "application submitted",
            "you applied",
        )
    ):
        # "already applied" in similar-jobs blurbs can false-positive — require control too.
        applied_btn = page.locator("button, a").filter(has_text=re.compile(r"^applied$", re.I))
        if await applied_btn.count() > 0:
            try:
                if await applied_btn.first.is_visible():
                    return True
            except Exception:
                pass
        if "you have already applied" in body or "you already applied" in body:
            return True
    applied_btn = page.locator("button, a").filter(has_text=re.compile(r"^applied$", re.I))
    if await applied_btn.count() > 0:
        try:
            if await applied_btn.first.is_visible():
                return True
        except Exception:
            pass
    return False


async def _wait_for_apply_button(page: Page, *, timeout_ms: int = 10_000) -> bool:
    try:
        await page.wait_for_function(
            """() => {
              const patterns = [/apply to this job/i, /apply now/i, /^apply$/i, /easy apply/i];
              for (const el of document.querySelectorAll('button, a[role="button"], a')) {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                const text = (el.innerText || el.textContent || '').trim();
                if (!text || text.length > 48) continue;
                if (patterns.some((p) => p.test(text))) return true;
              }
              return false;
            }""",
            timeout=timeout_ms,
        )
        return True
    except PlaywrightTimeout:
        return False


async def _click_apply(page: Page) -> bool:
    for pattern in _APPLY_PATTERNS:
        btn = page.get_by_role("button", name=re.compile(pattern, re.I))
        if await btn.count() > 0:
            try:
                await btn.first.click(timeout=5000)
                return True
            except PlaywrightTimeout:
                continue
    link = page.get_by_role("link", name=re.compile(r"apply now|apply to this job|^apply$", re.I))
    if await link.count() > 0:
        try:
            await link.first.click(timeout=5000)
            return True
        except PlaywrightTimeout:
            pass
    return False


async def _application_success(page: Page) -> bool:
    if await _already_applied(page):
        return True
    body = (await page.locator("body").inner_text()).lower()
    return any(
        phrase in body
        for phrase in (
            "successfully applied",
            "application submitted",
            "application sent",
            "thank you for applying",
            "applied successfully",
            "your application has been",
        )
    )


async def _resume_upload_modal_visible(page: Page) -> bool:
    body = (await page.locator("body").inner_text()).lower()
    if "upload your resume" in body or "upload resume" in body:
        file_inputs = page.locator('input[type="file"]')
        if await file_inputs.count() > 0:
            return True
    return False


async def _handle_resume_upload(page: Page, config: AppConfig) -> bool:
    if not await _resume_upload_modal_visible(page):
        return True
    if not config.resume_path.exists():
        logger.warning("Cutshort resume modal shown but resume missing: %s", config.resume_path)
        return False
    ok = await upload_resume(page, config.resume_path)
    if not ok:
        logger.warning("Cutshort: could not upload resume")
        return False
    await page.wait_for_timeout(1200)
    # After upload, Cutshort may auto-continue or show Confirm/Apply.
    await click_cutshort_advance(page)
    await page.wait_for_timeout(800)
    return True


def _queue_missing(
    config: AppConfig,
    job: JobListing,
    questions: list,
    answers: dict[str, str],
) -> list[str]:
    missing = [
        str(f.get("label", "")).strip()
        for f in questions
        if str(f.get("label", "")).strip() and not str(answers.get(str(f.get("label", "")).strip(), "")).strip()
    ]
    if missing:
        fields_by_label = {
            str(f.get("label", "")).strip(): f for f in questions if str(f.get("label", "")).strip()
        }
        queue_unanswered(
            config.base_dir,
            source="cutshort",
            job_title=job.title,
            company=job.company,
            job_url=job.url,
            labels=missing,
            job_id=job.job_id,
            fields_by_label=fields_by_label,
            config=config,
        )
        defer_job_for_run(config.applied_jobs_path, job, reason="need answers")
    return missing


async def goto_cutshort_job_detail(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    with contextlib.suppress(PlaywrightTimeout):
        await page.wait_for_selector("main, h1, button", timeout=10_000)
    await page.wait_for_timeout(400)


async def apply_to_job(
    page: Page,
    _context: BrowserContext | None,
    job: JobListing,
    config: AppConfig,
) -> bool | None:
    await goto_cutshort_job_detail(page, job.url)

    if await _already_applied(page):
        logger.info("Already applied on Cutshort: %s", job.title)
        return None

    jd = await _page_jd(page)

    if config.application.dry_run:
        logger.info("[DRY RUN] Would apply on Cutshort: %s", job.title)
        return None

    if not await _wait_for_apply_button(page):
        if await _already_applied(page):
            logger.info("Already applied on Cutshort: %s", job.title)
            return None
        logger.warning("No apply button on Cutshort: %s", job.url)
        return False

    if not await _click_apply(page):
        logger.warning("Could not click Apply on Cutshort: %s", job.url)
        return False

    await page.wait_for_timeout(1000)
    await prepare_interactive_page(page, fast=True)

    if not await _handle_resume_upload(page, config):
        from ..technical_failures import record_technical_failure

        record_technical_failure(
            config.base_dir,
            job_key=job_key(job.source, job.job_id),
            source="cutshort",
            title=job.title,
            company=job.company,
            url=job.url,
            reason="resume upload failed",
        )
        return False

    max_steps = 5
    step_delay = config.application.platform_delays.cutshort_step_ms
    for step in range(max_steps):
        if await _application_success(page):
            break

        questions = await discover_cutshort_questions(page, prepped=True)
        # Resume-only modal has no real screening questions.
        questions = [q for q in questions if "resume" not in str(q.get("label", "")).lower()]
        answers: dict[str, str] = {}
        if questions:
            logger.info(
                "Cutshort: %d question(s) for %s (step %d)",
                len(questions),
                job.title,
                step + 1,
            )
            answers = await resolve_question_answers(
                config,
                job,
                jd,
                questions,
                interactive=False,
                defer_new=True,
            )
            missing = _queue_missing(config, job, questions, answers)
            if missing:
                from ..run_issues import record_skip

                record_skip(
                    source="cutshort",
                    title=job.title,
                    company=job.company,
                    url=job.url,
                    reason="need answers",
                    questions=missing,
                )
                logger.warning(
                    "Skipped apply (need answers): %s — %d question(s) queued",
                    job.title,
                    len(missing),
                )
                return None
            unfilled = await fill_cutshort_questions(page, questions, answers)
            if unfilled:
                answered_unfilled = [u for u in unfilled if str(answers.get(u, "")).strip()]
                if answered_unfilled:
                    from ..technical_failures import record_technical_failure

                    record_technical_failure(
                        config.base_dir,
                        job_key=job_key(job.source, job.job_id),
                        source="cutshort",
                        title=job.title,
                        company=job.company,
                        url=job.url,
                        reason="could not fill answered field(s): "
                        + "; ".join(str(u)[:40] for u in answered_unfilled[:3]),
                    )
                return None

        if await _application_success(page):
            break

        action = await click_cutshort_advance(page)
        if not action:
            await page.wait_for_timeout(1200)
            if await _application_success(page):
                break
            if step == 0 and not questions:
                # One-click apply with no follow-up form — wait for Applied state.
                await page.wait_for_timeout(1500)
            break

        await page.wait_for_timeout(step_delay)

    if not await _application_success(page):
        await page.wait_for_timeout(1200)
        if not await _application_success(page):
            # Guest resume-upload may redirect into signup; treat as incomplete.
            body = (await page.locator("body").inner_text()).lower()
            if "candidate login" in body and await _resume_upload_modal_visible(page):
                logger.warning(
                    "Cutshort apply needs login/session for %s — run: python main.py login --platform cutshort",
                    job.title,
                )
            else:
                logger.warning("Could not confirm Cutshort submit for %s", job.url)
            from ..technical_failures import record_technical_failure

            record_technical_failure(
                config.base_dir,
                job_key=job_key(job.source, job.job_id),
                source="cutshort",
                title=job.title,
                company=job.company,
                url=job.url,
                reason="could not complete/submit application",
            )
            return False

    save_applied_job(
        config.applied_jobs_path,
        job_key("cutshort", job.job_id),
        {"source": "cutshort", "title": job.title, "company": job.company, "url": job.url},
    )
    logger.info("Applied on Cutshort: %s @ %s", job.title, job.company or "?")
    return True


async def apply_batch(
    page: Page,
    context: BrowserContext | None,
    jobs: list[JobListing],
    config: AppConfig,
) -> int:
    workers = max(1, config.application.cutshort_apply_workers)
    if workers > 1:
        logger.info("Cutshort parallel apply: %d workers, %d jobs", workers, len(jobs))
    return await run_apply_batch(
        jobs,
        config,
        page,
        context,
        apply_to_job,
        workers=workers,
    )
