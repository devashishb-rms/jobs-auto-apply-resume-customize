"""Smoke-test Cutshort search + one job apply path (dry by default).

Usage:
    python scripts/test_single_cutshort.py
    python scripts/test_single_cutshort.py https://cutshort.io/job/...
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs_auto_apply.browser import cutshort_session
from jobs_auto_apply.config import CutshortFiltersConfig, load_config
from jobs_auto_apply.cutshort.apply import (
    _already_applied,
    _click_apply,
    _wait_for_apply_button,
    goto_cutshort_job_detail,
)
from jobs_auto_apply.cutshort.search import collect_job_listings, feed_urls
from jobs_auto_apply.page_load import goto_settled
from jobs_auto_apply.utils import setup_logging


async def main() -> None:
    config = load_config(Path("config.yaml"))
    setup_logging(config.base_dir / "data" / "test_cutshort.log", verbose=True)
    cli_urls = [a for a in sys.argv[1:] if a.startswith("http")]

    async with cutshort_session(config) as (_browser, _context, page):
        if not cli_urls:
            filters = config.cutshort.filters
            if not isinstance(filters, CutshortFiltersConfig):
                print("Cutshort filters missing in config.yaml")
                return
            urls = feed_urls(filters)
            print(f"Feeds: {urls[:3]}{'…' if len(urls) > 3 else ''}")
            await goto_settled(page, urls[0])
            jobs = await collect_job_listings(page, limit=10)
            print(f"Collected {len(jobs)} listings")
            for job in jobs[:5]:
                print(f"  - {job.title} @ {job.company or '?'} — {job.url}")
            if not jobs:
                return
            cli_urls = [jobs[0].url]

        for url in cli_urls:
            print("\n" + "=" * 70)
            print(f"JOB: {url}")
            await goto_cutshort_job_detail(page, url)
            if await _already_applied(page):
                print("Already applied")
                continue
            has_btn = await _wait_for_apply_button(page)
            print(f"Apply button visible: {has_btn}")
            if has_btn and config.application.dry_run:
                print("[DRY RUN] Would click Apply — not submitting.")
            elif has_btn:
                clicked = await _click_apply(page)
                print(f"Clicked Apply: {clicked}")
                await page.wait_for_timeout(1500)
                body = (await page.locator("body").inner_text())[:400]
                print(f"Post-click snippet:\n{body}")


if __name__ == "__main__":
    asyncio.run(main())
