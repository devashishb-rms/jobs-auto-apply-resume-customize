from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import Page

from ..cookies import slugify
from ..page_load import goto_settled, scroll_lazy_page, wait_for_page_settled
from ..utils import JobListing
from .auth import CUTSHORT_ORIGIN
from .config import CutshortFiltersConfig

logger = logging.getLogger("job_apply")

_JOB_HREF = re.compile(r"/job/[A-Za-z0-9][A-Za-z0-9\-_%]+", re.I)
_COMPANY_HREF = re.compile(r"/company/", re.I)

# Common Cutshort city slugs (URL path segment after -jobs-in-).
_CITY_SLUGS: dict[str, str] = {
    "bangalore": "bangalore-bengaluru",
    "bengaluru": "bangalore-bengaluru",
    "bangalore bengaluru": "bangalore-bengaluru",
    "delhi": "delhi-ncr-gurgaon-noida",
    "delhi ncr": "delhi-ncr-gurgaon-noida",
    "gurgaon": "delhi-ncr-gurgaon-noida",
    "noida": "delhi-ncr-gurgaon-noida",
    "mumbai": "mumbai",
    "pune": "pune",
    "hyderabad": "hyderabad",
    "chennai": "chennai",
    "ahmedabad": "ahmedabad",
    "kolkata": "kolkata",
    "jaipur": "jaipur",
    "indore": "indore",
    "kochi": "kochi-cochin",
    "cochin": "kochi-cochin",
    "coimbatore": "coimbatore",
    "chandigarh": "chandigarh",
    "bhubaneswar": "bhubaneswar",
    "remote": "remote",
}


def _city_slug(city: str) -> str:
    key = city.strip().lower()
    if key in _CITY_SLUGS:
        return _CITY_SLUGS[key]
    return slugify(city)


def _paginated_url(base_url: str, page_num: int) -> str:
    if page_num <= 1:
        return base_url
    parsed = urlparse(base_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["page"] = [str(page_num)]
    query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=query))


def _keyword_search_url(keyword: str, city: str | None = None) -> str:
    kw = slugify(keyword)
    if not kw:
        kw = "software-engineer"
    if city:
        return f"{CUTSHORT_ORIGIN}/jobs/{kw}-jobs-in-{_city_slug(city)}"
    return f"{CUTSHORT_ORIGIN}/jobs/{kw}-jobs"


def feed_urls(filters: CutshortFiltersConfig) -> list[str]:
    if filters.search_urls:
        return list(filters.search_urls)
    keywords = filters.keywords or ["software engineer"]
    cities = filters.cities or [None]
    urls: list[str] = []
    for keyword in keywords:
        for city in cities:
            urls.append(_keyword_search_url(str(keyword), city))
    return urls


async def apply_filters(page: Page, filters: CutshortFiltersConfig) -> None:
    urls = feed_urls(filters)
    logger.info("Cutshort using %d search feed(s)", len(urls))
    await goto_settled(page, urls[0])
    await page.wait_for_timeout(600)
    logger.info("Cutshort filters applied: %s", page.url)


def _job_id(href: str) -> str:
    slug = href.rstrip("/").split("/")[-1]
    # Trailing opaque id is usually the last hyphen segment.
    parts = slug.rsplit("-", 1)
    if len(parts) == 2 and 6 <= len(parts[1]) <= 16 and parts[1].isalnum():
        return parts[1]
    return hashlib.sha1(href.encode()).hexdigest()[:16]


async def _company_near_link(page: Page, link) -> str:
    """Best-effort company name from the same card as the job title link."""
    try:
        company = await link.evaluate(
            """(el) => {
              let node = el;
              for (let i = 0; i < 6 && node; i++) {
                const anchors = node.querySelectorAll('a[href*="/company/"]');
                for (const a of anchors) {
                  const text = (a.innerText || '').trim();
                  if (text && text.length < 80) return text;
                }
                node = node.parentElement;
              }
              return '';
            }"""
        )
        return str(company or "").strip()
    except Exception:
        return ""


async def _merge_jobs_on_page(page: Page, jobs: dict[str, JobListing], limit: int) -> int:
    before = len(jobs)
    links = page.locator('a[href*="/job/"]')
    count = await links.count()
    for i in range(count):
        if len(jobs) >= limit:
            break
        link = links.nth(i)
        href = await link.get_attribute("href") or ""
        if not _JOB_HREF.search(href) or _COMPANY_HREF.search(href):
            continue
        full_url = urljoin(CUTSHORT_ORIGIN, href.split("?")[0])
        text = (await link.inner_text()).strip()
        if len(text) < 3:
            continue
        title = text.split("\n")[0].strip()
        if len(title) < 3:
            continue
        # Skip city/category directory links that look like job pages.
        if re.search(r"\bjobs?\s+in\b", title, re.I):
            continue
        job_id = _job_id(full_url)
        if job_id in jobs:
            continue
        company = await _company_near_link(page, link)
        jobs[job_id] = JobListing(
            job_id=job_id,
            title=title,
            company=company,
            url=full_url,
            source="cutshort",
            easy_apply=True,
        )
    return len(jobs) - before


async def collect_srp_page(page: Page, limit: int = 500) -> list[JobListing]:
    jobs: dict[str, JobListing] = {}
    await _merge_jobs_on_page(page, jobs, limit)
    await scroll_lazy_page(page, rounds=3, pause_ms=250)
    await wait_for_page_settled(page, extra_ms=200)
    await _merge_jobs_on_page(page, jobs, limit)
    return list(jobs.values())


async def iter_paginated_feed_pages(
    page: Page,
    filters: CutshortFiltersConfig,
    *,
    max_pages: int | None = None,
) -> AsyncIterator[tuple[str, int, list[JobListing]]]:
    pages_cap = max_pages if max_pages is not None else filters.max_pages
    if pages_cap < 1:
        pages_cap = 1

    for feed_url in feed_urls(filters):
        for page_num in range(1, pages_cap + 1):
            url = _paginated_url(feed_url, page_num)
            logger.info("Cutshort feed page %d: %s", page_num, url)
            await goto_settled(page, url, timeout_ms=90_000, quick=page_num > 1)
            jobs = await collect_srp_page(page)
            if not jobs:
                if page_num > 1:
                    logger.info("Cutshort feed ended at page %d (no listings)", page_num)
                break
            yield feed_url, page_num, jobs


async def collect_job_listings(page: Page, limit: int) -> list[JobListing]:
    jobs: dict[str, JobListing] = {}
    await _merge_jobs_on_page(page, jobs, limit)
    stable = 0
    for round_num in range(30):
        if len(jobs) >= limit:
            break
        before = len(jobs)
        await page.mouse.wheel(0, 2500)
        await scroll_lazy_page(page, rounds=2, pause_ms=200)
        await wait_for_page_settled(page, extra_ms=200)
        await _merge_jobs_on_page(page, jobs, limit)
        if len(jobs) == before:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
            if round_num % 8 == 7:
                logger.info("Cutshort scrolling… %d listings so far", len(jobs))
    listings = list(jobs.values())[:limit]
    logger.info("Found %d Cutshort listings on page", len(listings))
    return listings


async def collect_from_search_urls(page: Page, urls: list[str], limit: int) -> list[JobListing]:
    merged: dict[str, JobListing] = {}
    per_feed = limit if limit > 0 else 2000
    for url in urls:
        await goto_settled(page, url, timeout_ms=90_000)
        for job in await collect_job_listings(page, per_feed):
            merged.setdefault(job.job_id, job)
            if limit > 0 and len(merged) >= limit:
                return list(merged.values())[:limit]
    return list(merged.values())[:limit] if limit > 0 else list(merged.values())
