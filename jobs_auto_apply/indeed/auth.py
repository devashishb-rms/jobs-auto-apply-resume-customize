from __future__ import annotations

import re
from pathlib import Path

from playwright.async_api import BrowserContext, Page

from ..cookies import inject_cookies as _inject

# Indeed supports plain email/password sign-in (plus optional Google/Apple/Facebook
# OAuth as alternatives, not a hard requirement) — unlike Wellfound/Uplers, it is not
# Google-OAuth-only, so this follows Hirist's cookie-based session pattern rather than
# the Chrome-profile pattern.
#
# Indeed serves country-specific subdomains (www.indeed.com for the US,
# in.indeed.com for India, uk.indeed.com, etc). This tool is India-focused
# (config.example.yaml ships `l=India`), so the default domain here is
# in.indeed.com rather than the US site. The actual domain used at runtime is
# always config-driven — see IndeedFiltersConfig.domain in config.py — these
# module-level constants are just the fallback default for callers that don't
# have a config/domain handy.
DEFAULT_INDEED_DOMAIN = "in.indeed.com"


def indeed_origin(domain: str | None = None) -> str:
    """Build the Indeed origin URL for a given country domain (e.g. 'in.indeed.com')."""
    return f"https://{(domain or DEFAULT_INDEED_DOMAIN).strip()}"


def indeed_login_url(domain: str | None = None) -> str:
    return f"{indeed_origin(domain)}/account/login"


def indeed_cookie_domain(domain: str | None = None) -> str:
    return f".{(domain or DEFAULT_INDEED_DOMAIN).strip()}"


# Back-compat module-level defaults (India domain) for callers that don't thread
# a config-provided domain through (e.g. import-time usage). Prefer
# indeed_origin(filters.domain) / indeed_login_url(filters.domain) wherever a
# config is available.
INDEED_ORIGIN = indeed_origin()
INDEED_LOGIN = indeed_login_url()


async def inject_cookies(context: BrowserContext, cookies_path: Path, domain: str | None = None) -> None:
    await _inject(
        context,
        cookies_path,
        default_domain=indeed_cookie_domain(domain),
        required_names=None,
    )


async def verify_logged_in(page: Page, expected_name: str, domain: str | None = None) -> bool:
    """Best-effort login check.

    NOT verified against a real logged-in Indeed session (no test account was
    available). Indeed's live search results were reachable for anonymous
    browsing during research, but every request hit a Cloudflare "Verify you are
    human" checkpoint before any authenticated page could be inspected — bypassing
    that checkpoint is out of scope, so the DOM below (account menu text, "Sign in"
    link) is inferred from Indeed's well-documented public layout, not confirmed
    live. Re-check this against the real DOM after the user's first
    `python main.py login --platform indeed` and adjust the selectors if the
    heuristics below misfire.
    """
    origin = indeed_origin(domain)
    for url in (f"{origin}/", f"{origin}/jobs", origin):
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        if "/account/login" in page.url.lower():
            continue
        # Cloudflare/interstitial "Verify you are human" checkpoint — never a
        # logged-in state, and never something this tool should try to solve.
        body = (await page.locator("body").inner_text()).lower()
        if "verify you are human" in body or "additional verification required" in body:
            continue
        login_link = page.get_by_role("link", name=re.compile(r"^sign in$", re.I))
        if await login_link.count() > 0 and await login_link.first.is_visible():
            continue
        if expected_name.lower() in body or "sign out" in body or "my jobs" in body or "applied" in body:
            return True
        account_menu = page.locator('[data-testid="AccountMenu"], #gnav-menu-link-account, [id*="AccountMenu" i]')
        if await account_menu.count() > 0:
            return True
        if await login_link.count() == 0:
            return True
    return False
