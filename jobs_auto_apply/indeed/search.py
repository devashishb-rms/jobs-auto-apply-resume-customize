"""Indeed search/listing collection.

IMPORTANT — not verified by live DOM inspection. The plan for this package was to
open indeed.com's public (no-login) search results with Playwright and read the
real URL scheme + job-card DOM, the same way every other site package in this repo
was built. That inspection could not be completed: every request to indeed.com and
in.indeed.com (even a bare search-results URL, no login) was intercepted by a
Cloudflare "Verify you are human" interstitial (a Turnstile checkbox challenge)
before any real content loaded. Bypassing/completing a CAPTCHA is out of scope for
this tool, so the URL parameters and selectors below are built from Indeed's
well-documented, long-stable public conventions instead of a live-inspected DOM:

  * ``q``      — free-text keywords
  * ``l``      — location (city/region text, or blank for "All of <country>")
  * ``start``  — pagination offset, in increments of 10 (page 1 omits it)
  * ``fromage``— "posted within N days"
  * ``sort``   — "date" (newest first) or left unset (relevance, Indeed's default)
  * job cards expose a stable ``data-jk`` attribute (Indeed's canonical per-job key)
    on the result container / title link — this has been Indeed's job identifier
    for years and is the most reliable anchor available without a live DOM.

Treat every selector here as a first draft: re-verify against the real DOM after
the user's first `python main.py login --platform indeed` (or even an anonymous
`run --platform indeed` once the Cloudflare check has been solved once in a real
browser and the session/cookies persist), and adjust the selector fallbacks below
if listings come back empty or malformed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from urllib.parse import quote_plus, urljoin

from playwright.async_api import Page

from ..config import IndeedFiltersConfig
from ..page_load import goto_settled, scroll_lazy_page, wait_for_page_settled
from ..utils import JobListing
from .auth import DEFAULT_INDEED_DOMAIN, indeed_origin

logger = logging.getLogger("job_apply")

_JOBCARD_CONTAINER = (
    '[data-testid="slider_item"], .job_seen_beacon, .jobsearch-SerpJobCard, .cardOutline, li[data-jk]'
)
_TITLE_LINK = 'a.jcs-JobTitle, h2.jobTitle a, a[id^="job_"], a[data-jk]'
_COMPANY_SEL = '[data-testid="company-name"], .companyName, [class*="company"]'
_EASY_APPLY_TEXT = re.compile(r"easily apply|easy apply", re.I)


def _search_url(filters: IndeedFiltersConfig) -> str:
    params: list[str] = []
    if filters.keywords:
        params.append(f"q={quote_plus(filters.keywords)}")
    if filters.locations:
        params.append(f"l={quote_plus(filters.locations[0])}")
    if filters.max_job_age_days:
        params.append(f"fromage={int(filters.max_job_age_days)}")
    if filters.sort and filters.sort.lower() != "relevance":
        params.append(f"sort={quote_plus(filters.sort)}")
    query = "&".join(params)
    base = f"{indeed_origin(filters.domain)}/jobs"
    return f"{base}?{query}" if query else base


def _paginated_url(base_url: str, page_num: int) -> str:
    """Indeed paginates search results with ?start=N in increments of 10 (page 1 omits it)."""
    if page_num <= 1:
        return base_url
    start = (page_num - 1) * 10
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}start={start}"


def feed_urls(filters: IndeedFiltersConfig) -> list[str]:
    if filters.search_urls:
        return list(filters.search_urls)
    if filters.locations and len(filters.locations) > 1:
        urls = []
        origin = indeed_origin(filters.domain)
        for loc in filters.locations:
            params = []
            if filters.keywords:
                params.append(f"q={quote_plus(filters.keywords)}")
            params.append(f"l={quote_plus(loc)}")
            if filters.max_job_age_days:
                params.append(f"fromage={int(filters.max_job_age_days)}")
            if filters.sort and filters.sort.lower() != "relevance":
                params.append(f"sort={quote_plus(filters.sort)}")
            urls.append(f"{origin}/jobs?{'&'.join(params)}")
        return urls
    return [_search_url(filters)]


async def apply_filters(page: Page, filters: IndeedFiltersConfig) -> None:
    """Indeed's filters are fully URL-driven (q/l/fromage/sort) — no on-page UI to click."""
    if filters.search_urls:
        logger.info("Indeed using %d configured search URL(s)", len(filters.search_urls))
        return
    url = _search_url(filters)
    await goto_settled(page, url)
    await page.wait_for_timeout(600)
    logger.info("Indeed filters applied: %s", page.url)


def _job_id(card_jk: str, href: str, title: str) -> str:
    if card_jk:
        return card_jk
    import hashlib

    return hashlib.sha1(f"{href}|{title}".encode()).hexdigest()[:16]


async def _merge_jobs_on_page(
    page: Page,
    jobs: dict[str, JobListing],
    limit: int,
    *,
    easy_apply_only: bool,
    origin: str | None = None,
    stats: dict[str, int] | None = None,
) -> int:
    origin = origin or indeed_origin()
    before = len(jobs)
    cards = page.locator(_JOBCARD_CONTAINER)
    count = await cards.count()
    for i in range(count):
        if len(jobs) >= limit:
            break
        card = cards.nth(i)
        try:
            jk = (await card.get_attribute("data-jk")) or ""
            title_link = card.locator(_TITLE_LINK)
            if not jk and await title_link.count() > 0:
                jk = (await title_link.first.get_attribute("data-jk")) or ""
            href = ""
            title = ""
            if await title_link.count() > 0:
                href = (await title_link.first.get_attribute("href")) or ""
                title = (await title_link.first.inner_text()).strip()
            if not title:
                continue
            title = title.split("\n")[0].strip()
            if len(title) < 3:
                continue

            card_text = (await card.inner_text()).strip()
            if stats is not None:
                stats["seen"] = stats.get("seen", 0) + 1
            if easy_apply_only and not _EASY_APPLY_TEXT.search(card_text):
                if stats is not None:
                    stats["rejected_easy_apply"] = stats.get("rejected_easy_apply", 0) + 1
                continue

            company = ""
            company_loc = card.locator(_COMPANY_SEL)
            if await company_loc.count() > 0:
                company = (await company_loc.first.inner_text()).strip()

            url = f"{origin}/viewjob?jk={jk}" if jk else urljoin(origin, href.split("?")[0])
            job_id = _job_id(jk, url, title)
            jobs.setdefault(
                job_id,
                JobListing(
                    job_id=job_id,
                    title=title,
                    company=company,
                    url=url,
                    source="indeed",
                    easy_apply=bool(_EASY_APPLY_TEXT.search(card_text)),
                ),
            )
        except Exception:
            continue
    return len(jobs) - before


def _warn_if_all_filtered(stats: dict[str, int], kept: int, *, context: str) -> None:
    """§Issue 5: an easy_apply_only filter that silently rejects every card is a
    broken-selector symptom, not a genuinely-empty result set — log loudly so a
    stale badge selector is debuggable on the first run instead of just showing 0
    jobs with no explanation.
    """
    seen = stats.get("seen", 0)
    if seen > 0 and kept == 0:
        logger.warning(
            "Indeed easy_apply_only filter rejected all %d job card(s) on %s — "
            "the 'Easily apply' badge selector may be stale; check "
            "indeed/search.py's _EASY_APPLY_TEXT pattern against the live DOM.",
            seen,
            context,
        )


async def collect_srp_page(
    page: Page,
    limit: int = 500,
    *,
    easy_apply_only: bool = False,
    domain: str | None = None,
) -> list[JobListing]:
    """Collect jobs on the current SRP page only (no cross-page infinite scroll)."""
    origin = indeed_origin(domain)
    jobs: dict[str, JobListing] = {}
    stats: dict[str, int] = {}
    await _merge_jobs_on_page(page, jobs, limit, easy_apply_only=easy_apply_only, origin=origin, stats=stats)
    await scroll_lazy_page(page, rounds=2, pause_ms=200)
    await wait_for_page_settled(page, extra_ms=200)
    await _merge_jobs_on_page(page, jobs, limit, easy_apply_only=easy_apply_only, origin=origin, stats=stats)
    if easy_apply_only:
        _warn_if_all_filtered(stats, len(jobs), context=page.url)
    return list(jobs.values())


async def iter_job_listings(
    page: Page, limit: int, *, easy_apply_only: bool = False, domain: str | None = None
) -> AsyncIterator[JobListing]:
    origin = indeed_origin(domain)
    jobs: dict[str, JobListing] = {}
    stats: dict[str, int] = {}
    await _merge_jobs_on_page(page, jobs, limit, easy_apply_only=easy_apply_only, origin=origin, stats=stats)
    for listing in list(jobs.values()):
        yield listing

    stable = 0
    for round_num in range(40):
        if len(jobs) >= limit:
            break
        before = set(jobs.keys())
        await page.mouse.wheel(0, 2500)
        await scroll_lazy_page(page, rounds=2, pause_ms=200)
        await wait_for_page_settled(page, extra_ms=200)
        await _merge_jobs_on_page(page, jobs, limit, easy_apply_only=easy_apply_only, origin=origin, stats=stats)
        added = len(jobs) - len(before)
        if added == 0:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
            if round_num % 8 == 7:
                logger.info("Indeed scrolling… %d listings so far", len(jobs))
            for job_id in jobs:
                if job_id not in before:
                    yield jobs[job_id]

    if easy_apply_only:
        _warn_if_all_filtered(stats, len(jobs), context=page.url)


async def collect_job_listings(
    page: Page, limit: int, *, easy_apply_only: bool = False, domain: str | None = None
) -> list[JobListing]:
    """Collect all listings on current page with infinite scroll (legacy single-page scrape)."""
    seen: set[str] = set()
    listings: list[JobListing] = []
    async for job in iter_job_listings(page, limit, easy_apply_only=easy_apply_only, domain=domain):
        if job.job_id in seen:
            continue
        seen.add(job.job_id)
        listings.append(job)
        if len(listings) >= limit:
            break
    logger.info("Found %d Indeed listings on page", len(listings))
    return listings[:limit]


async def collect_from_search_urls(
    page: Page, urls: list[str], limit: int, *, easy_apply_only: bool = False, domain: str | None = None
) -> list[JobListing]:
    merged: dict[str, JobListing] = {}
    per_feed = limit if limit > 0 else 2000
    for feed_idx, url in enumerate(urls):
        logger.info("Indeed feed: %s", url)
        await goto_settled(page, url, timeout_ms=90_000, quick=feed_idx > 0)
        for job in await collect_srp_page(page, per_feed, easy_apply_only=easy_apply_only, domain=domain):
            merged[job.job_id] = job
        if limit > 0 and len(merged) >= limit:
            break
    result = list(merged.values())[:limit] if limit > 0 else list(merged.values())
    logger.info("Found %d Indeed listings across %d feed(s)", len(result), len(urls))
    return result


async def iter_paginated_feed_pages(
    page: Page,
    filters: IndeedFiltersConfig,
    *,
    max_pages: int | None = None,
) -> AsyncIterator[tuple[str, int, list[JobListing]]]:
    """Yield (feed_url, page_num, jobs) — apply after each page before continuing."""
    pages_cap = max_pages if max_pages is not None else filters.max_pages
    if pages_cap < 1:
        pages_cap = 1

    for feed_url in feed_urls(filters):
        for page_num in range(1, pages_cap + 1):
            url = _paginated_url(feed_url, page_num)
            logger.info("Indeed feed page %d: %s", page_num, url)
            await goto_settled(page, url, timeout_ms=90_000, quick=page_num > 1)
            jobs = await collect_srp_page(page, easy_apply_only=filters.easy_apply_only, domain=filters.domain)
            if not jobs:
                if page_num > 1:
                    logger.info("Indeed feed ended at page %d (no listings)", page_num)
                break
            yield feed_url, page_num, jobs
