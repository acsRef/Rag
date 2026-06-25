"""LLM client base classes with circuit breaker and health tracking.

Circuit breaker states:
  CLOSED → (N consecutive failures) → OPEN → (cooldown) → HALF_OPEN → (probe)
    ↑                                                                    │
    └──────────────── probe succeeds ────────────────────────────────────┘
    └──────────────── probe fails ─────→ OPEN (reset cooldown)

Failures counted: 5xx errors, timeouts, connection errors (NOT 4xx).
"""

import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"           # normal operation
    OPEN = "open"               # fast-fail, no requests allowed
    HALF_OPEN = "half_open"     # probing after cooldown


@dataclass
class CircuitBreaker:
    """Per-provider circuit breaker state machine."""

    failure_threshold: int = 10
    cooldown_seconds: float = 30.0

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    _total_failures: int = 0   # lifetime counter for diagnostics
    _total_successes: int = 0  # lifetime counter for diagnostics

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allow_request(self) -> bool:
        """Check whether a new request should be attempted."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    "Circuit breaker HALF_OPEN (probing after %.1fs cooldown)",
                    elapsed,
                )
                return True
            return False
        # HALF_OPEN — allow exactly one probe
        return True

    def on_success(self) -> None:
        """Record a successful request."""
        self._total_successes += 1
        if self.state != CircuitState.CLOSED:
            logger.info("Circuit breaker CLOSED (recovered)")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_success_time = time.monotonic()

    def on_failure(self) -> None:
        """Record a failed request."""
        self._total_failures += 1
        self.failure_count += 1
        self.last_failure_time = time.monotonic()

        if self.state == CircuitState.HALF_OPEN:
            # Probe failed — go back to OPEN
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN (probe failed, total_failures=%d)",
                self._total_failures,
            )
        elif self.failure_count >= self.failure_threshold and self.state == CircuitState.CLOSED:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN (threshold=%d reached, total_failures=%d)",
                self.failure_threshold, self._total_failures,
            )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a dict suitable for diagnostics JSON."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
        }


class CircuitOpenError(Exception):
    """Raised when a request is blocked by an open circuit breaker."""
    pass


# ------------------------------------------------------------------
# Global provider health
# ------------------------------------------------------------------

class ProviderHealth:
    """Global tracker of circuit breakers keyed by provider name."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, provider: str) -> CircuitBreaker:
        if provider not in self._breakers:
            self._breakers[provider] = CircuitBreaker(
                failure_threshold=settings.circuit_breaker_threshold,
                cooldown_seconds=settings.circuit_breaker_cooldown,
            )
        return self._breakers[provider]

    def is_degraded(self) -> list[str]:
        """Return list of provider names currently degraded."""
        return [
            name for name, breaker in self._breakers.items()
            if breaker.state != CircuitState.CLOSED
        ]

    def snapshot_all(self) -> dict[str, Any]:
        """Snapshot of all breaker states (for diagnostics)."""
        return {name: breaker.snapshot() for name, breaker in self._breakers.items()}


provider_health = ProviderHealth()


# ------------------------------------------------------------------
# Retry helpers
# ------------------------------------------------------------------

def _should_retry(exc: Exception, status_code: int | None = None) -> bool:
    """Determine whether an exception is transient and worth retrying.

    Never retry 4xx errors — those are permanent (bad request, auth failure).
    Retry 5xx, timeouts, and connection-level errors.
    """
    if isinstance(exc, CircuitOpenError):
        return False
    if status_code is not None:
        if 400 <= status_code < 500:
            return False
        if status_code >= 500:
            return True
    # Timeout / connection errors from httpx / OpenAI SDK
    name = type(exc).__name__.lower()
    return any(kw in name for kw in ("timeout", "connection", "network", "read"))


def jittered_backoff(attempt: int, base: float = 1.0) -> float:
    """Exponential backoff with jitter."""
    return base * (2 ** attempt) + random.uniform(0, 0.5)
