"""Unit tests for resilience/policy.py — every branch of SDA §A5.3's decision
table. should_proceed is a pure function (no I/O, no wall-clock reads), so
these tests construct Signals dataclasses directly with no mocking.

Run: python -m unittest tests.test_resilience_policy -v
"""

from __future__ import annotations

import unittest

from jobs_auto_apply.platform_adapter import BlockingEvent, BlockingEventType
from jobs_auto_apply.resilience.policy import Action, Signals, should_proceed


def _event(event_type: str) -> BlockingEvent:
    return BlockingEvent(type=event_type, platform="naukri", job_key="naukri:1")


class TestShouldProceed(unittest.TestCase):
    def test_transient_dom_is_bot_retry(self) -> None:
        d = should_proceed(_event(BlockingEventType.TRANSIENT_DOM), Signals(9999, False, 60, 0))
        self.assertEqual(d.action, Action.RETRY)
        self.assertEqual(d.max_attempts, 2)
        self.assertEqual(d.backoff_base_seconds, 2.0)
        self.assertEqual(d.backoff_cap_seconds, 30.0)

    def test_rate_limited_is_bot_retry(self) -> None:
        d = should_proceed(_event(BlockingEventType.RATE_LIMITED), Signals(0, True, 60, 0))
        self.assertEqual(d.action, Action.RETRY)

    def test_captcha_human_present_waits(self) -> None:
        d = should_proceed(
            _event(BlockingEventType.CAPTCHA),
            Signals(human_recent_activity_seconds=100, time_of_day_in_active_hours=True, historical_reply_latency_p50=60, platform_recent_failure_rate=0),
        )
        self.assertEqual(d.action, Action.WAIT_AND_POLL)
        self.assertEqual(d.timeout_seconds, min(120.0, 3 * 60))

    def test_captcha_human_absent_notifies(self) -> None:
        d = should_proceed(
            _event(BlockingEventType.CAPTCHA),
            Signals(human_recent_activity_seconds=9999, time_of_day_in_active_hours=True, historical_reply_latency_p50=60, platform_recent_failure_rate=0),
        )
        self.assertEqual(d.action, Action.NOTIFY_AND_DEFER)
        self.assertTrue(d.requeue_job)

    def test_captcha_outside_active_hours_notifies_even_if_recent(self) -> None:
        d = should_proceed(
            _event(BlockingEventType.CAPTCHA),
            Signals(human_recent_activity_seconds=10, time_of_day_in_active_hours=False, historical_reply_latency_p50=60, platform_recent_failure_rate=0),
        )
        self.assertEqual(d.action, Action.NOTIFY_AND_DEFER)

    def test_otp_email_same_branch_as_captcha(self) -> None:
        d = should_proceed(
            _event(BlockingEventType.OTP_EMAIL),
            Signals(human_recent_activity_seconds=10, time_of_day_in_active_hours=True, historical_reply_latency_p50=10, platform_recent_failure_rate=0),
        )
        self.assertEqual(d.action, Action.WAIT_AND_POLL)

    def test_google_2fa_push_same_branch_as_captcha(self) -> None:
        d = should_proceed(
            _event(BlockingEventType.GOOGLE_2FA_PUSH),
            Signals(human_recent_activity_seconds=9999, time_of_day_in_active_hours=True, historical_reply_latency_p50=10, platform_recent_failure_rate=0),
        )
        self.assertEqual(d.action, Action.NOTIFY_AND_DEFER)

    def test_session_dead_never_requeues(self) -> None:
        d = should_proceed(_event(BlockingEventType.SESSION_DEAD), Signals(0, True, 60, 0))
        self.assertEqual(d.action, Action.NOTIFY_AND_DEFER)
        self.assertFalse(d.requeue_job)

    def test_unknown_event_type_abandons(self) -> None:
        d = should_proceed(_event("SOMETHING_NEW"), Signals(0, True, 60, 0))
        self.assertEqual(d.action, Action.ABANDON_WITH_LOG)
        self.assertEqual(d.reason, "SOMETHING_NEW")

    def test_zero_blind_retry_against_human_only_events(self) -> None:
        """The single most safety-critical property: no HUMAN_ONLY event type
        ever produces a RETRY decision, under any signal combination."""
        for event_type in BlockingEventType.HUMAN_ONLY:
            for activity in (0, 100, 299, 300, 9999):
                for active_hours in (True, False):
                    d = should_proceed(
                        _event(event_type),
                        Signals(activity, active_hours, 60, 0),
                    )
                    self.assertNotEqual(
                        d.action,
                        Action.RETRY,
                        f"{event_type} with activity={activity} active_hours={active_hours} produced RETRY",
                    )


if __name__ == "__main__":
    unittest.main()
