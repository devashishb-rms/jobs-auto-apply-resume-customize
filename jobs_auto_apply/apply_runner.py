from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from playwright.async_api import BrowserContext, Page

from .config import AppConfig
from .run_issues import record_run_attempt
from .technical_failures import record_technical_failure
from .utils import JobListing, job_key

logger = logging.getLogger("job_apply")

# v3 adaptive retry (SDA §A5) is feature-flagged; keep these imports lazy/local
# inside the functions that use them so importing apply_runner.py has zero new
# behavior/cost when application.adaptive_retry_enabled is false (the default).

ApplyFn = Callable[
    [Page, BrowserContext | None, JobListing, AppConfig],
    Awaitable[bool | None],
]


class ApplyBatchStopped(Exception):
    """Raised to stop applying to further jobs in a batch."""


class BrowserClosed(ApplyBatchStopped):
    """Raised when the page/context/browser was closed mid-run (e.g. user quit Chrome)."""


def is_target_closed_error(exc: BaseException) -> bool:
    """True when a Playwright error means the page/context/browser is gone."""
    name = type(exc).__name__
    if name in ("TargetClosedError", "TargetClosed"):
        return True
    msg = str(exc).lower()
    return (
        "has been closed" in msg
        or "target page, context or browser" in msg
        or "target closed" in msg
        or "browser has been closed" in msg
    )


async def _apply_job_with_retries(
    job: JobListing,
    config: AppConfig,
    page: Page,
    context: BrowserContext | None,
    apply_one: ApplyFn,
    *,
    max_attempts: int,
    retry_backoff_ms: int = 1500,
    label: str = "",
) -> bool:
    prefix = f"{label} " if label else ""
    for attempt in range(1, max_attempts + 1):
        try:
            result = await apply_one(page, context, job, config)
        except ApplyBatchStopped:
            raise
        except Exception as exc:
            if is_target_closed_error(exc):
                # The page/context/browser was closed (often the user reopened
                # Chrome mid-run). Retrying is pointless and would crash on the
                # dead page; stop the whole batch instead of flagging this job.
                logger.error(
                    "%sBrowser/page closed during apply for %s — stopping run. "
                    "Do not open Chrome while a run is in progress.",
                    prefix,
                    job.title,
                )
                raise BrowserClosed("browser/page closed during apply") from exc
            if attempt < max_attempts:
                logger.warning(
                    "%sApply error for %s @ %s (attempt %d/%d), retrying...",
                    prefix,
                    job.title,
                    job.company,
                    attempt,
                    max_attempts,
                )
                await asyncio.sleep(retry_backoff_ms / 1000)
                continue
            logger.exception("%sFailed applying to %s after %d attempts", prefix, job.url, max_attempts)
            record_technical_failure(
                config.base_dir,
                job_key=job_key(job.source, job.job_id),
                source=job.source,
                title=job.title,
                company=job.company,
                url=job.url,
                reason=f"exception: {type(exc).__name__}: {str(exc)[:120]}",
            )
            return False

        if result is True:
            record_run_attempt(job_key(job.source, job.job_id))
            return True
        if result is None:
            record_run_attempt(job_key(job.source, job.job_id))
            return None
        if attempt < max_attempts:
            logger.warning(
                "%sApply failed for %s @ %s (attempt %d/%d), retrying...",
                prefix,
                job.title,
                job.company,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(retry_backoff_ms / 1000)
        else:
            logger.warning(
                "%sSkipping %s @ %s after %d failed attempts",
                prefix,
                job.title,
                job.company,
                max_attempts,
            )
    record_run_attempt(job_key(job.source, job.job_id))
    record_technical_failure(
        config.base_dir,
        job_key=job_key(job.source, job.job_id),
        source=job.source,
        title=job.title,
        company=job.company,
        url=job.url,
        reason=f"apply not completed after {max_attempts} attempt(s)",
    )
    return False


async def _apply_job_adaptive(
    job: JobListing,
    config: AppConfig,
    page: Page,
    context: BrowserContext | None,
    apply_one: ApplyFn,
    *,
    label: str = "",
) -> bool:
    """Adaptive-retry path (SDA §A5), used only when
    ``application.adaptive_retry_enabled`` is true. Routes any detected
    blocking event through ``resilience.should_proceed`` instead of the flat
    ``apply_retries`` count. Falls back to a single flat retry pass (identical
    in shape to the non-adaptive path) for failures where no blocking event is
    detected, since not every failure is a §A5 event — most are still the
    ordinary flaky-DOM case §A5.1 says is fine to retry normally.
    """
    from .platform_adapter import get_adapter
    from .resilience import Action, collect_signals, get_circuit_breaker, should_proceed
    from .telegram import send_notification

    prefix = f"{label} " if label else ""
    platform = job.source
    breaker = get_circuit_breaker(config)
    adapter = get_adapter(platform)
    backoff_ms = max(0, config.application.retry_backoff_ms)

    async def _detect_event():
        if adapter is None:
            return None
        try:
            return await adapter.detect_blocking_event(page)
        except Exception:
            return None

    attempt = 1
    max_flat_attempts = 1 + max(0, config.application.apply_retries)
    while True:
        try:
            result = await apply_one(page, context, job, config)
        except ApplyBatchStopped:
            raise
        except Exception as exc:
            if is_target_closed_error(exc):
                logger.error(
                    "%sBrowser/page closed during apply for %s — stopping run.",
                    prefix,
                    job.title,
                )
                raise BrowserClosed("browser/page closed during apply") from exc

            event = await _detect_event()
            if event is None:
                # Not a recognized blocking event — ordinary technical failure,
                # same bounded-flat-retry behavior as the non-adaptive path.
                breaker.record(platform, success=False)
                if attempt < max_flat_attempts:
                    attempt += 1
                    await asyncio.sleep(backoff_ms / 1000)
                    continue
                logger.exception("%sFailed applying to %s after %d attempts", prefix, job.url, attempt)
                record_technical_failure(
                    config.base_dir,
                    job_key=job_key(job.source, job.job_id),
                    source=job.source,
                    title=job.title,
                    company=job.company,
                    url=job.url,
                    reason=f"exception: {type(exc).__name__}: {str(exc)[:120]}",
                )
                return False

            decision = should_proceed(event, collect_signals(config, platform=platform, breaker=breaker))
            handled = await _apply_decision(
                decision, event, job, config, prefix, breaker=breaker, send_notification=send_notification
            )
            if handled.retry:
                attempt += 1
                continue
            return handled.result

        if result is True:
            record_run_attempt(job_key(job.source, job.job_id))
            breaker.record(platform, success=True)
            return True
        if result is None:
            record_run_attempt(job_key(job.source, job.job_id))
            return None

        # result is False — check for a blocking event before falling back to
        # the ordinary flat-retry path.
        event = await _detect_event()
        if event is None:
            breaker.record(platform, success=False)
            if attempt < max_flat_attempts:
                attempt += 1
                await asyncio.sleep(backoff_ms / 1000)
                continue
            logger.warning("%sSkipping %s @ %s after %d failed attempts", prefix, job.title, job.company, attempt)
            record_run_attempt(job_key(job.source, job.job_id))
            record_technical_failure(
                config.base_dir,
                job_key=job_key(job.source, job.job_id),
                source=job.source,
                title=job.title,
                company=job.company,
                url=job.url,
                reason=f"apply not completed after {attempt} attempt(s)",
            )
            return False

        decision = should_proceed(event, collect_signals(config, platform=platform, breaker=breaker))
        handled = await _apply_decision(
            decision, event, job, config, prefix, breaker=breaker, send_notification=send_notification
        )
        if handled.retry:
            attempt += 1
            continue
        return handled.result


class _DecisionOutcome:
    def __init__(self, *, retry: bool, result: bool) -> None:
        self.retry = retry
        self.result = result


async def _apply_decision(decision, event, job, config, prefix, *, breaker, send_notification) -> _DecisionOutcome:
    """Carry out one resilience.Decision. Returns whether to retry in-loop and
    the final result to use if not retrying."""
    from .resilience import Action

    if decision.action == Action.RETRY:
        # Bounded exponential retry against a bot-resolvable event (TRANSIENT_DOM
        # / RATE_LIMITED) — never against a human-only event (see policy.py).
        backoff = min(decision.backoff_cap_seconds, decision.backoff_base_seconds)
        logger.info(
            "%sBlocking event %s for %s @ %s — bot-resolvable, retrying (backoff %.1fs)",
            prefix,
            event.type,
            job.title,
            job.company,
            backoff,
        )
        await asyncio.sleep(backoff)
        breaker.record(job.source, success=False)
        return _DecisionOutcome(retry=True, result=False)

    if decision.action == Action.WAIT_AND_POLL:
        wait_seconds = min(decision.timeout_seconds, config.resilience.wait_poll_max_seconds)
        logger.info(
            "%sBlocking event %s for %s @ %s — human plausibly available, waiting up to %.0fs",
            prefix,
            event.type,
            job.title,
            job.company,
            wait_seconds,
        )
        await asyncio.sleep(wait_seconds)
        # A short synchronous wait is the best we can safely do without a page
        # re-check loop that risks acting on a stale page reference across an
        # await boundary; the job is deferred either way if the challenge is
        # still present on the next attempt (handled by the caller's normal
        # failure path on this same attempt).
        send_notification(
            config,
            f"[jobs-auto-apply] {event.type} on {job.source} for '{job.title}' @ {job.company}. "
            "Waited briefly for you to resolve it — will retry next run if still blocked.",
        )
        breaker.record(job.source, success=False)
        return _DecisionOutcome(retry=False, result=False)

    if decision.action == Action.NOTIFY_AND_DEFER:
        requeue_note = (
            "Will retry automatically on the next scheduled run."
            if decision.requeue_job
            else f"Needs a fresh login — run `python main.py login --platform {job.source}`."
        )
        send_notification(
            config,
            f"[jobs-auto-apply] {event.type} on {job.source} for '{job.title}' @ {job.company}. {requeue_note}",
        )
        logger.warning(
            "%sBlocking event %s for %s @ %s — notified and deferred (requeue=%s)",
            prefix,
            event.type,
            job.title,
            job.company,
            decision.requeue_job,
        )
        breaker.record(job.source, success=False)
        # Leave the job un-applied (not recorded in applied_jobs) — it is
        # naturally retried on the next scheduled run rather than requiring a
        # separate requeue mechanism.
        return _DecisionOutcome(retry=False, result=False)

    # ABANDON_WITH_LOG
    breaker.record(job.source, success=False)
    record_technical_failure(
        config.base_dir,
        job_key=job_key(job.source, job.job_id),
        source=job.source,
        title=job.title,
        company=job.company,
        url=job.url,
        reason=f"blocking_event abandoned: {decision.reason}",
    )
    logger.error("%sAbandoning %s @ %s: %s", prefix, job.title, job.company, decision.reason)
    return _DecisionOutcome(retry=False, result=False)


async def run_apply_batch(
    jobs: list[JobListing],
    config: AppConfig,
    page: Page,
    context: BrowserContext | None,
    apply_one: ApplyFn,
    *,
    workers: int | None = None,
) -> int:
    """Apply to jobs. Uses parallel tabs when workers > 1. No delay between jobs."""
    if not jobs:
        return 0

    max_attempts = 1 + max(0, config.application.apply_retries)
    backoff_ms = max(0, config.application.retry_backoff_ms)
    workers = max(1, workers if workers is not None else config.application.apply_workers)

    adaptive = bool(config.application.adaptive_retry_enabled)
    if adaptive:
        from .resilience import get_circuit_breaker

        breaker = get_circuit_breaker(config)
        platform = jobs[0].source
        if breaker.is_open(platform):
            logger.warning(
                "Circuit breaker open for %s (cooldown %.0fs remaining) — skipping %d job(s) this run",
                platform,
                breaker.cooldown_remaining_seconds(platform),
                len(jobs),
            )
            return 0

    async def _apply_one(job: JobListing, tab: Page, *, label: str = "") -> bool | None:
        # Resume-tailoring shadow/live hook (§A4, feature-flagged) wraps the
        # apply call so it can optionally override which résumé file
        # config.resume_path resolves to for the duration of this one call.
        from .tailoring import tailor_for_job

        async with tailor_for_job(job, config):
            if adaptive:
                return await _apply_job_adaptive(job, config, tab, context, apply_one, label=label)
            return await _apply_job_with_retries(
                job,
                config,
                tab,
                context,
                apply_one,
                max_attempts=max_attempts,
                retry_backoff_ms=backoff_ms,
                label=label,
            )

    if workers <= 1 or context is None:
        applied = 0
        for job in jobs:
            try:
                result = await _apply_one(job, page)
                if result is True:
                    applied += 1
            except ApplyBatchStopped as exc:
                logger.warning("%s — stopping apply batch after %d success(es)", exc, applied)
                break
        return applied

    sem = asyncio.Semaphore(workers)
    total = len(jobs)

    async def _one(job: JobListing, index: int) -> bool | None:
        async with sem:
            try:
                tab = await context.new_page()
            except Exception as exc:
                if is_target_closed_error(exc):
                    raise BrowserClosed("context closed before opening tab") from exc
                raise
            label = f"[{index}/{total}]"
            try:
                logger.info("%s Applying: %s @ %s", label, job.title, job.company)
                return await _apply_one(job, tab, label=label)
            finally:
                with contextlib.suppress(Exception):
                    await tab.close()

    results = await asyncio.gather(*[_one(job, i) for i, job in enumerate(jobs, 1)], return_exceptions=True)
    applied = sum(1 for ok in results if ok is True)
    if any(isinstance(r, ApplyBatchStopped) for r in results):
        logger.error(
            "Parallel apply stopped early — browser/page was closed. Applied %d before the browser closed.",
            applied,
        )
    else:
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("Apply worker error: %s: %s", type(r).__name__, r)
    logger.info("Parallel apply finished: %d/%d succeeded (%d workers)", applied, total, workers)
    return applied
