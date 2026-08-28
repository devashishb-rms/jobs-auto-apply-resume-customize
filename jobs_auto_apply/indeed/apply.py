"""Indeed apply flow.

Indeed job postings resolve to one of two apply shapes:

  * "Indeed Apply" — a native multi-step application wizard hosted on Indeed
    (historically ``smartapply.indeed.com`` / an ``#indeedapply-widget`` iframe on
    the job page itself). Steps typically include: résumé (reuse-on-file or
    upload), contact info confirmation, employer screening questions, a review
    page, then submit.
  * "Apply on company site" — Indeed redirects (usually via a "Company Site"
    button/new tab) to the employer's own careers page / ATS, exactly like
    Wellfound/Uplers' external-apply paths already handled by
    ``jobs_auto_apply/ats/apply.py``.

NOT verified against a real logged-in Indeed session — see search.py and
questions.py module docstrings for why (Cloudflare's "Verify you are human"
checkpoint blocked all live inspection, including anonymous/logged-out pages,
and bypassing it is out of scope). The button text/selectors and step-by-step
DOM below follow Indeed's long-documented public "Indeed Apply" pattern
(``button#indeedApplyButton``, an iframe/frame whose URL contains
"indeedapply" or "smartapply"), but must be re-verified and adjusted after the
user's first real ``python main.py run --platform indeed`` — exactly like a
first real run against any newly added platform in this codebase typically
surfaces adjustments.
"""

from __future__ import annotations

import contextlib
import logging
import re

from playwright.async_api import BrowserContext, Frame, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..application_questions import resolve_question_answers
from ..apply_runner import run_apply_batch
from ..ats.apply import apply_on_company_site
from ..config import AppConfig
from ..page_load import prepare_interactive_page
from ..pending_questions import queue_unanswered
from ..utils import JobListing, defer_job_for_run, job_key, save_applied_job
from .questions import QuestionDiscoveryError, discover_indeed_questions, fill_indeed_questions
from .resume import attach_resume_on_apply_step

logger = logging.getLogger("job_apply")

Surface = Page | Frame

_NATIVE_APPLY_BTN = re.compile(r"^apply now$|^apply$", re.I)
_EXTERNAL_APPLY_BTN = re.compile(r"apply on company site|company site", re.I)
_CONTINUE_BTN = re.compile(r"^continue$|^next$|^review\b|^submit\b|^submit your application$", re.I)
_ALREADY_APPLIED = re.compile(r"you('|’)ve applied|already applied|application submitted", re.I)
_SUCCESS_TEXT = re.compile(
    r"application submitted|your application has been submitted|you('|’)ve applied|application sent",
    re.I,
)


async def _page_jd(page: Page) -> str:
    for sel in (
        "#jobDescriptionText",
        '[class*="jobDescription" i]',
        "article",
        "main",
    ):
        loc = page.locator(sel)
        if await loc.count() > 0:
            text = (await loc.first.inner_text()).strip()
            if len(text) > 120:
                return text[:12000]
    return (await page.locator("body").inner_text())[:12000]


async def _already_applied(page: Page) -> bool:
    body = (await page.locator("body").inner_text()).lower()
    return bool(_ALREADY_APPLIED.search(body))


async def goto_indeed_job_detail(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    with contextlib.suppress(PlaywrightTimeout):
        await page.wait_for_selector("main, #jobDescriptionText, body", timeout=10_000)
    await page.wait_for_timeout(400)


async def _find_apply_surface(page: Page, *, timeout_ms: int = 8000) -> Surface | None:
    """Locate whichever surface (main page, iframe, or popup) hosts the Indeed Apply widget.

    Indeed has historically embedded the widget as an iframe on the job page. If
    that iframe never appears (e.g. Indeed instead opens a full navigation or a
    new tab in the account's real flow), the caller falls back to operating on
    ``page`` directly.
    """
    deadline_ms = timeout_ms
    step = 500
    waited = 0
    while waited < deadline_ms:
        for frame in page.frames:
            url = (frame.url or "").lower()
            if "indeedapply" in url or "smartapply" in url:
                return frame
        await page.wait_for_timeout(step)
        waited += step
    return None


async def _click_native_apply(page: Page) -> bool:
    btn = page.get_by_role("button", name=_NATIVE_APPLY_BTN)
    if await btn.count() > 0:
        try:
            await btn.first.click(timeout=5000)
            return True
        except PlaywrightTimeout:
            pass
    fallback = page.locator("#indeedApplyButton, button, a").filter(has_text=_NATIVE_APPLY_BTN)
    if await fallback.count() > 0:
        try:
            await fallback.first.click(timeout=5000)
            return True
        except PlaywrightTimeout:
            pass
    return False


async def _has_external_apply_link(page: Page) -> bool:
    """Cheap, non-clicking check for whether this listing routes to a company site."""
    link = page.locator("a, button").filter(has_text=_EXTERNAL_APPLY_BTN)
    return await link.count() > 0


async def _external_apply_url(page: Page) -> str | None:
    link = page.locator("a, button").filter(has_text=_EXTERNAL_APPLY_BTN)
    if await link.count() == 0:
        return None
    href = await link.first.get_attribute("href")
    if href:
        return href
    # Some listings open the company site in a new tab on click rather than a plain href.
    # This opens a real popup page just to read its URL — always close it here so it
    # never leaks, especially with apply_workers running multiple jobs in parallel.
    popup = None
    try:
        async with page.context.expect_page(timeout=5000) as popup_info:
            await link.first.click(timeout=5000)
        popup = await popup_info.value
        await popup.wait_for_load_state("domcontentloaded", timeout=15000)
        return popup.url
    except PlaywrightTimeout:
        return None
    finally:
        if popup is not None:
            with contextlib.suppress(Exception):
                await popup.close()


async def _click_continue(surface: Surface) -> bool:
    btn = surface.get_by_role("button", name=_CONTINUE_BTN)
    if await btn.count() > 0:
        for i in range(await btn.count() - 1, -1, -1):
            candidate = btn.nth(i)
            try:
                if not await candidate.is_visible():
                    continue
                await candidate.click(timeout=5000)
                return True
            except PlaywrightTimeout:
                continue
    return False


async def _surface_success(surface: Surface) -> bool:
    try:
        body = surface.locator("body")
        text = (await body.inner_text()).lower()
    except Exception:
        return False
    return bool(_SUCCESS_TEXT.search(text))


def _unanswered_labels(questions: list, answers: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for field in questions:
        label = str(field.get("label", "")).strip()
        if not label:
            continue
        if answers.get(label, "").strip():
            continue
        missing.append(label)
    return missing


def _queue_missing(
    config: AppConfig,
    job: JobListing,
    questions: list,
    answers: dict[str, str],
    *,
    reason: str = "need answers",
) -> list[str]:
    missing = _unanswered_labels(questions, answers)
    if missing:
        fields_by_label = {str(f.get("label", "")).strip(): f for f in questions if str(f.get("label", "")).strip()}
        queue_unanswered(
            config.base_dir,
            source="indeed",
            job_title=job.title,
            company=job.company,
            job_url=job.url,
            labels=missing,
            job_id=job.job_id,
            fields_by_label=fields_by_label,
            config=config,
        )
        defer_job_for_run(config.applied_jobs_path, job, reason=reason)
    return missing


async def _run_native_apply_steps(
    page: Page,
    surface: Surface,
    job: JobListing,
    jd: str,
    config: AppConfig,
) -> bool | None:
    """Walk the Indeed Apply wizard's steps. Returns True/False/None like apply_to_job."""
    await attach_resume_on_apply_step(page, config.resume_path, scope=surface if surface is not page else None)

    max_steps = 8
    for step in range(max_steps):
        if await _surface_success(surface):
            return True

        try:
            questions = await discover_indeed_questions(surface)
        except QuestionDiscoveryError as exc:
            # §Issue 3: a discovery *error* is not the same as "no questions on
            # this step" — surface.evaluate() genuinely failing must never fall
            # through to _click_continue (whose regex matches "Submit"), or a
            # real, unanswered screening question could get silently submitted.
            # Abort this apply attempt and queue it for manual/pending review
            # instead of guessing the step is clear.
            from ..technical_failures import record_technical_failure

            record_technical_failure(
                config.base_dir,
                job_key=job_key(job.source, job.job_id),
                source="indeed",
                title=job.title,
                company=job.company,
                url=job.url,
                reason=f"question discovery failed on step {step + 1} (aborted to avoid a blind submit): {exc}",
            )
            logger.warning(
                "Skipped apply (question discovery failed, step %d): %s — %s",
                step + 1,
                job.title,
                exc,
            )
            return None
        answers: dict[str, str] = {}
        if questions:
            logger.info("Indeed: %d question(s) for %s (step %d)", len(questions), job.title, step + 1)
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
                    source="indeed",
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

            unfilled = await fill_indeed_questions(surface, questions, answers)
            if unfilled:
                from ..technical_failures import record_technical_failure

                record_technical_failure(
                    config.base_dir,
                    job_key=job_key(job.source, job.job_id),
                    source="indeed",
                    title=job.title,
                    company=job.company,
                    url=job.url,
                    reason=f"could not fill {len(unfilled)} answered field(s) (selection/DOM failed): "
                    + "; ".join(str(u)[:40] for u in unfilled[:3]),
                )
                logger.warning(
                    "Skipped apply (could not fill %d answered field(s)): %s",
                    len(unfilled),
                    job.title,
                )
                return None

        if await _surface_success(surface):
            return True

        advanced = await _click_continue(surface)
        if not advanced:
            await page.wait_for_timeout(1200)
            if await _surface_success(surface):
                return True
            if step == 0:
                logger.warning("No Indeed Apply Continue/Submit control found for %s", job.url)
                return False
            break

        await page.wait_for_timeout(700)
        if await _surface_success(surface):
            return True

    if await _surface_success(surface):
        return True

    logger.warning("Could not confirm Indeed Apply submission for %s", job.url)
    from ..technical_failures import record_technical_failure

    record_technical_failure(
        config.base_dir,
        job_key=job_key(job.source, job.job_id),
        source="indeed",
        title=job.title,
        company=job.company,
        url=job.url,
        reason="could not complete/submit Indeed Apply wizard (success state never appeared)",
    )
    return False


async def apply_to_job(
    page: Page,
    context: BrowserContext | None,
    job: JobListing,
    config: AppConfig,
) -> bool | None:
    await goto_indeed_job_detail(page, job.url)

    if await _already_applied(page):
        logger.info("Already applied on Indeed: %s", job.title)
        return None

    jd = await _page_jd(page)

    if config.application.dry_run:
        logger.info("[DRY RUN] Would apply on Indeed: %s", job.title)
        return None

    # §Issue 2: check skip_external_ats before ever probing/clicking to open a
    # popup — the cheap href check is fine either way, but the popup-click
    # fallback in _external_apply_url should never run when we're just going to
    # skip the listing anyway (it also matters because apply_workers can run
    # several of these concurrently, each one an extra orphaned tab).
    if await _has_external_apply_link(page):
        if config.application.skip_external_ats:
            logger.info("Skipping external-ATS Indeed listing (skip_external_ats): %s", job.title)
            return None
        external_url = await _external_apply_url(page)
        if external_url:
            logger.info("Indeed listing routes to company site: %s", external_url)
            applied = await apply_on_company_site(page, job=job, config=config, url=external_url)
            if applied:
                save_applied_job(
                    config.applied_jobs_path,
                    job_key("indeed", job.job_id),
                    {"source": "indeed", "title": job.title, "company": job.company, "url": job.url},
                )
                logger.info("Applied on Indeed (company site): %s @ %s", job.title, job.company or "?")
                return True
            return False

    if not await _click_native_apply(page):
        if await _already_applied(page):
            logger.info("Already applied on Indeed: %s", job.title)
            return None
        logger.warning("No Indeed Apply button found on %s", job.url)
        return False

    surface = await _find_apply_surface(page)
    if surface is None:
        surface = page
    else:
        with contextlib.suppress(Exception):
            await prepare_interactive_page(page, fast=True)

    result = await _run_native_apply_steps(page, surface, job, jd, config)
    if result is True:
        save_applied_job(
            config.applied_jobs_path,
            job_key("indeed", job.job_id),
            {"source": "indeed", "title": job.title, "company": job.company, "url": job.url},
        )
        logger.info("Applied on Indeed: %s @ %s", job.title, job.company or "?")
    return result


async def apply_batch(
    page: Page,
    context: BrowserContext | None,
    jobs: list[JobListing],
    config: AppConfig,
) -> int:
    workers = max(1, config.application.indeed_apply_workers)
    if workers > 1:
        logger.info("Indeed parallel apply: %d workers, %d jobs", workers, len(jobs))
    return await run_apply_batch(
        jobs,
        config,
        page,
        context,
        apply_to_job,
        workers=workers,
    )
