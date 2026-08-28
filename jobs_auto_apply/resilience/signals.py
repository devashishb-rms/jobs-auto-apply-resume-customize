"""Signal collection for the adaptive human-intervention engine (SDA §A5.2).

All wall-clock/file-I/O reads live here, in one place — this is the module
that owns "is a human currently available", per §A9's anti-pattern table
("Global mutable state for 'is a human currently available'... Centralized in
resilience/signals.py, one clear ownership boundary"). ``policy.should_proceed``
never reads the clock or touches the filesystem itself; it only receives the
``Signals`` dataclass this module produces.

Judgment call — human_recent_activity_seconds source (documented per the
implementation prompt's explicit request): the SDA offers two options, "a
heartbeat file touched by an active RDP/SSH session" or "last Telegram reply
timestamp from pending_questions.py's existing history". This implementation
uses the **mtime of the pending-questions state file**
(``config.pending_questions_path``, e.g. ``data/pending_questions.json``) as
the proxy. Reasoning: a heartbeat file requires a *new* mechanism (nothing in
this codebase currently touches a file on RDP/SSH activity — building and
wiring that reliably is its own project), whereas the pending-questions file
is already written every time a human interacts with the bot's Q&A flow
(a new question queued, a reply processed, a group answered/dropped/ignored)
— it is already a reliable, low-noise proxy for "the user is actively working
with this bot" without adding any new instrumentation. It is not a perfect
signal (it doesn't capture RDP/SSH-only activity that never touches a pending
question), but it is the simpler-to-wire-up-correctly choice, exactly as the
prompt asked to prioritize.

historical_reply_latency_p50 has no real tracked history yet in v2 (no field
records how long a Telegram reply took to arrive). Rather than build a new
latency-tracking store in this pass, this returns
``config.resilience.default_reply_latency_seconds`` — a configurable constant
— and this gap is flagged explicitly in the final implementation report as a
reasonable follow-up (a real p50 could be derived once cost_ledger/telegram
message timestamps are correlated over enough runs to be statistically
meaningful; a handful of data points would be noise).
"""

from __future__ import annotations

import time
from datetime import datetime

from ..config import AppConfig
from .circuit_breaker import CircuitBreaker
from .policy import Signals


def _human_recent_activity_seconds(config: AppConfig) -> float:
    path = config.pending_questions_path
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # No pending-questions file has ever been written — treat as "long ago"
        # rather than "just now", so we don't falsely assume a human is present.
        return float("inf")
    return max(0.0, time.time() - mtime)


def _time_of_day_in_active_hours(config: AppConfig, *, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    start = config.resilience.active_hours_start
    end = config.resilience.active_hours_end
    hour = now.hour
    if start <= end:
        return start <= hour < end
    # wraps past midnight (e.g. start=22, end=6)
    return hour >= start or hour < end


def collect_signals(
    config: AppConfig,
    *,
    platform: str,
    breaker: CircuitBreaker | None = None,
) -> Signals:
    """Gather the five §A5.2 signals for one blocking event."""
    failure_rate = breaker.failure_rate(platform) if breaker is not None else 0.0
    return Signals(
        human_recent_activity_seconds=_human_recent_activity_seconds(config),
        time_of_day_in_active_hours=_time_of_day_in_active_hours(config),
        historical_reply_latency_p50=float(config.resilience.default_reply_latency_seconds),
        platform_recent_failure_rate=failure_rate,
        human_recent_activity_threshold_seconds=float(config.resilience.human_recent_activity_threshold_seconds),
    )
