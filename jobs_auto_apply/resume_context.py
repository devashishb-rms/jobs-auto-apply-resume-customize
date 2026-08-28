"""Per-task override for which résumé file gets uploaded (v3 tailoring subsystem).

``AppConfig.resume_path`` normally always resolves to the single static
``resume.pdf`` configured in ``resume.path``. The tailoring subsystem (Phase 3)
needs to swap in a JD-tailored PDF for the duration of a single job's apply
call, without touching any of the six site packages' DOM logic (which all read
``config.resume_path`` directly today).

A ``contextvars.ContextVar`` is used instead of a global/thread-local so the
override is correctly scoped per concurrent apply task (each parallel apply
worker runs in its own asyncio Task, and ContextVar values are copied into new
tasks at creation time and isolated afterwards). When no override is active —
which is always true unless the tailoring subsystem's live-swap mode
(``resume_tailoring_shadow_mode: false``) is on — ``resume_path`` behaves
exactly as before.
"""

from __future__ import annotations

import contextvars
from pathlib import Path

_active_resume_path: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "active_resume_path", default=None
)


def get_active_resume_override() -> Path | None:
    return _active_resume_path.get()


class use_resume_path:
    """Context manager: temporarily override the résumé path for this task."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._token: contextvars.Token | None = None

    def __enter__(self) -> use_resume_path:
        self._token = _active_resume_path.set(self._path)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _active_resume_path.reset(self._token)
