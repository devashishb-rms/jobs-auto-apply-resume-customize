from __future__ import annotations

import re
from pathlib import Path

from playwright.async_api import BrowserContext, Page

from ..cookies import inject_cookies as _inject

CUTSHORT_ORIGIN = "https://cutshort.io"
CUTSHORT_LOGIN = f"{CUTSHORT_ORIGIN}/"
CUTSHORT_PROFILE = f"{CUTSHORT_ORIGIN}/profile/job-search"


async def inject_cookies(context: BrowserContext, cookies_path: Path) -> None:
    await _inject(
        context,
        cookies_path,
        default_domain=".cutshort.io",
        required_names=None,
    )


async def verify_logged_in(page: Page, expected_name: str) -> bool:
    for url in (CUTSHORT_PROFILE, CUTSHORT_ORIGIN, f"{CUTSHORT_ORIGIN}/jobs"):
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        if "/login" in page.url.lower():
            continue

        candidate_login = page.get_by_role("button", name=re.compile(r"candidate\s*login", re.I))
        if await candidate_login.count() > 0:
            try:
                if await candidate_login.first.is_visible():
                    continue
            except Exception:
                pass

        body = (await page.locator("body").inner_text()).lower()
        name = (expected_name or "").strip().lower()
        if name and name in body:
            return True
        if any(
            marker in body
            for marker in (
                "logout",
                "log out",
                "sign out",
                "my profile",
                "job preferences",
                "applications",
            )
        ):
            return True
        # Logged-in candidates land on /profile/* without a Candidate login CTA.
        if "/profile" in page.url.lower() and await candidate_login.count() == 0:
            return True
    return False
