from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import AppConfig


def _facts_path(config: AppConfig) -> Path:
    return config.base_dir / config.paths.application_facts


@lru_cache(maxsize=8)
def _load_yaml(path_str: str, mtime_ns: int) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def load_application_facts(config: AppConfig) -> dict[str, Any]:
    """Load profile/application_facts.yaml (empty dict if missing)."""
    path = _facts_path(config)
    if not path.exists():
        return {}
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return dict(_load_yaml(str(path.resolve()), mtime_ns))


def clear_application_facts_cache() -> None:
    _load_yaml.cache_clear()
