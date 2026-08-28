"""Adaptive Human-Intervention Signal Engine (SDA §A5).

Public surface: ``should_proceed(event, signals) -> Decision``. Everything
else (signal collection, the circuit breaker) is an implementation detail
consumed by ``apply_runner.py`` when ``application.adaptive_retry_enabled``
is true.
"""

from .circuit_breaker import CircuitBreaker, get_circuit_breaker
from .policy import Action, Decision, Signals, should_proceed
from .signals import collect_signals

__all__ = [
    "Action",
    "CircuitBreaker",
    "Decision",
    "Signals",
    "collect_signals",
    "get_circuit_breaker",
    "should_proceed",
]
