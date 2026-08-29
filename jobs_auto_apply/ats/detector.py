"""ATS vendor detection — loads jobs_auto_apply/ats/vendors.yaml instead of a
hardcoded Python list (Tier 2 of the platform-scaling refactor). Adding ATS
vendor #31 is now "add a YAML entry", not "add a Python branch" — see
vendors.yaml's header comment for the schema and for why apply.py's
vendor-specific "open apply form" click text moved in here too.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

VENDORS_PATH = Path(__file__).resolve().parent / "vendors.yaml"


@dataclass(frozen=True)
class AtsVendor:
    name: str
    patterns: tuple[re.Pattern[str], ...]
    open_apply_text: re.Pattern[str] | None = None


_lock = threading.Lock()
_cache: list[AtsVendor] | None = None


def _load_vendors(path: Path = VENDORS_PATH) -> list[AtsVendor]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("vendors", []) if isinstance(raw, dict) else []
    vendors: list[AtsVendor] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        patterns = entry.get("patterns") or []
        compiled = tuple(re.compile(str(p), re.I) for p in patterns if p)
        if not compiled:
            continue
        open_apply_text = entry.get("open_apply_text")
        vendors.append(
            AtsVendor(
                name=str(entry["name"]),
                patterns=compiled,
                open_apply_text=re.compile(str(open_apply_text), re.I) if open_apply_text else None,
            )
        )
    return vendors


def load_vendors(*, force_reload: bool = False) -> list[AtsVendor]:
    """Return the parsed vendor list, cached after first load.

    ``force_reload`` is for tests that want to point at a different YAML file
    via a monkeypatched ``VENDORS_PATH`` without process restart.
    """
    global _cache
    with _lock:
        if _cache is None or force_reload:
            _cache = _load_vendors()
        return _cache


def detect_ats(url: str) -> str:
    for vendor in load_vendors():
        if any(pattern.search(url) for pattern in vendor.patterns):
            return vendor.name
    host = urlparse(url).netloc.lower()
    if host and "careers" in host:
        return "careers_portal"
    return "generic"


def open_apply_text_for(ats: str) -> re.Pattern[str] | None:
    """The vendor-specific "open apply form" button/link text pattern, if any
    (Greenhouse/Lever/Ashby today) — None means the caller should use its own
    generic fallback pattern, exactly like the old hardcoded ``_open_apply_form``
    branch did for every vendor not explicitly listed.
    """
    for vendor in load_vendors():
        if vendor.name == ats:
            return vendor.open_apply_text
    return None


__all__ = ["AtsVendor", "detect_ats", "load_vendors", "open_apply_text_for"]
