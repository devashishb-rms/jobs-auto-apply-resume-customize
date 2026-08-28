"""Per-platform rolling failure-rate circuit breaker (SDA §A5.2, §B2).

v2 has no explicit circuit-breaker module — ``apply_runner.py`` and
``technical_failures.py`` were checked first (per the implementation prompt's
instruction to reuse what exists before building from scratch): both track
*persistent, deduped* failure history (append-only, keyed by job), which is
the right shape for "has this specific job failed before" but the wrong shape
for "is this platform failing unusually often right now" — there is no
rolling time-window failure-rate tracker anywhere in v2. This module adds
that missing piece; it is new, not a reuse of an existing implementation, but
it deliberately mirrors technical_failures.py's style (simple in-memory
+ nothing fancier than needed for a single-process, single-run tool).

Even *resolvable* failures, if they cluster past a rate threshold on one
platform within a rolling window, should pause that platform for a cooldown
— this is what protects against a platform's own rate-limiting/bot-heuristics
regardless of any single event's correctness (§A5.4).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Window:
    events: deque = field(default_factory=deque)  # (timestamp, success: bool)


class CircuitBreaker:
    """In-memory, per-run rolling failure-rate breaker, one per platform."""

    def __init__(
        self,
        *,
        window_minutes: int = 30,
        failure_rate_threshold: float = 0.5,
        min_attempts: int = 5,
        cooldown_minutes: int = 30,
    ) -> None:
        self._window_seconds = max(60, window_minutes * 60)
        self._threshold = failure_rate_threshold
        self._min_attempts = max(1, min_attempts)
        self._cooldown_seconds = max(0, cooldown_minutes * 60)
        self._lock = threading.Lock()
        self._windows: dict[str, _Window] = {}
        self._tripped_until: dict[str, float] = {}

    def _prune(self, window: _Window, now: float) -> None:
        cutoff = now - self._window_seconds
        while window.events and window.events[0][0] < cutoff:
            window.events.popleft()

    def record(self, platform: str, *, success: bool) -> None:
        now = time.time()
        with self._lock:
            window = self._windows.setdefault(platform, _Window())
            window.events.append((now, success))
            self._prune(window, now)
            if not success and self.failure_rate(platform) >= self._threshold:
                attempts = len(window.events)
                if attempts >= self._min_attempts:
                    self._tripped_until[platform] = now + self._cooldown_seconds

    def failure_rate(self, platform: str) -> float:
        with self._lock:
            window = self._windows.get(platform)
            if not window or not window.events:
                return 0.0
            now = time.time()
            self._prune(window, now)
            if not window.events:
                return 0.0
            failures = sum(1 for _, ok in window.events if not ok)
            return failures / len(window.events)

    def is_open(self, platform: str) -> bool:
        """True while this platform is in a cooldown (should be skipped)."""
        with self._lock:
            until = self._tripped_until.get(platform)
            if until is None:
                return False
            if time.time() >= until:
                del self._tripped_until[platform]
                return False
            return True

    def cooldown_remaining_seconds(self, platform: str) -> float:
        with self._lock:
            until = self._tripped_until.get(platform)
            if until is None:
                return 0.0
            return max(0.0, until - time.time())


# One process-wide breaker per platform config, shared across a run so
# parallel apply workers all see the same rolling window.
_BREAKERS: dict[tuple, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def get_circuit_breaker(config) -> CircuitBreaker:  # AppConfig, avoiding import cycle at module scope
    r = config.resilience
    key = (
        r.circuit_breaker_window_minutes,
        r.circuit_breaker_failure_rate_threshold,
        r.circuit_breaker_min_attempts,
        r.circuit_breaker_cooldown_minutes,
    )
    with _BREAKERS_LOCK:
        breaker = _BREAKERS.get(key)
        if breaker is None:
            breaker = CircuitBreaker(
                window_minutes=r.circuit_breaker_window_minutes,
                failure_rate_threshold=r.circuit_breaker_failure_rate_threshold,
                min_attempts=r.circuit_breaker_min_attempts,
                cooldown_minutes=r.circuit_breaker_cooldown_minutes,
            )
            _BREAKERS[key] = breaker
        return breaker
