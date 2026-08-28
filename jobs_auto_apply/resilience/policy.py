"""Decision policy for blocking events (SDA §A5.3).

``should_proceed`` is a pure function: no I/O, no wall-clock reads. All inputs
(the event and the pre-computed signals) are passed in, so it is trivially
unit-testable — see tests/test_resilience_policy.py, which exercises every
branch of the decision table below without mocking anything.

This is the single most safety-critical piece of the whole v3 feature: it is
what guarantees **zero blind retries against a human-only blocking event**
(CAPTCHA, OTP email, Google 2FA push). Retrying against those is itself the
bot-detection signature that risks getting an account flagged — see §A5.1,
§A5.3, §A9 ("Retry storm").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..platform_adapter import BlockingEvent, BlockingEventType


class Action(str, Enum):
    RETRY = "RETRY"
    WAIT_AND_POLL = "WAIT_AND_POLL"
    NOTIFY_AND_DEFER = "NOTIFY_AND_DEFER"
    ABANDON_WITH_LOG = "ABANDON_WITH_LOG"


@dataclass(frozen=True)
class Signals:
    """§A5.2's five signals, pre-computed by signals.py and passed in."""

    human_recent_activity_seconds: float
    time_of_day_in_active_hours: bool
    historical_reply_latency_p50: float
    platform_recent_failure_rate: float
    # Static resolvability table result (challenge_type_resolvability) is
    # derived directly from event.type in should_proceed, not carried as a
    # separate signal field — it's a pure function of the event, not an
    # observed/measured quantity like the other four.


@dataclass(frozen=True)
class Decision:
    action: Action
    # RETRY
    max_attempts: int = 0
    backoff_base_seconds: float = 0.0
    backoff_cap_seconds: float = 0.0
    # WAIT_AND_POLL
    timeout_seconds: float = 0.0
    # NOTIFY_AND_DEFER
    requeue_job: bool = False
    # ABANDON_WITH_LOG
    reason: str = ""


def should_proceed(event: BlockingEvent, signals: Signals) -> Decision:
    """Pure decision function implementing SDA §A5.3's exact table."""

    if event.type in BlockingEventType.BOT_RESOLVABLE:
        # inherently bot-resolvable — bounded exponential retry, NOT a human-wait
        return Decision(
            action=Action.RETRY,
            max_attempts=2,
            backoff_base_seconds=2.0,
            backoff_cap_seconds=30.0,
        )

    if event.type in BlockingEventType.HUMAN_ONLY:
        # inherently human-only — never blind-retry against these (this IS the
        # bot-detection signature the user is worried about)
        if signals.human_recent_activity_seconds < 300 and signals.time_of_day_in_active_hours:
            return Decision(
                action=Action.WAIT_AND_POLL,
                timeout_seconds=min(120.0, 3 * signals.historical_reply_latency_p50),
            )
        return Decision(action=Action.NOTIFY_AND_DEFER, requeue_job=True)
        # job is left un-applied (not marked in applied_jobs) so it is naturally
        # retried on the NEXT scheduled run rather than in a tight loop now —
        # this reuses v2's existing "not-yet-applied" state instead of a new
        # requeue mechanism, see apply_runner.py's wiring.

    if event.type == BlockingEventType.SESSION_DEAD:
        # never worth retrying the same run — always needs a fresh login
        return Decision(action=Action.NOTIFY_AND_DEFER, requeue_job=False)
        # surfaces "re-run `python main.py login --platform X`" in the notify
        # message — matches v2's existing login flow, no new auth mechanism.

    return Decision(action=Action.ABANDON_WITH_LOG, reason=event.type)
