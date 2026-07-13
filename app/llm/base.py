"""LLM client base classes with circuit breaker, health tracking, and retry utilities.

Circuit breaker states:
  CLOSED → (N consecutive failures) → OPEN → (cooldown) → HALF_OPEN → (probe)
    ↑                                                                    │
    └──────────────── probe succeeds ────────────────────────────────────┘
    └──────────────── probe fails ─────→ OPEN (reset cooldown)

Failures counted: 5xx errors, timeouts, connection errors (NOT 4xx).
"""

import asyncio
import json
import logging
import random
import re
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
    _probe_in_flight: bool = False  # guard: only one HALF_OPEN probe at a time

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
                self._probe_in_flight = False
                logger.info(
                    "Circuit breaker HALF_OPEN (probing after %.1fs cooldown)",
                    elapsed,
                )
                return True
            return False
        # HALF_OPEN — allow exactly one probe
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def on_success(self) -> None:
        """Record a successful request."""
        self._total_successes += 1
        self._probe_in_flight = False
        if self.state != CircuitState.CLOSED:
            logger.info("Circuit breaker CLOSED (recovered)")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_success_time = time.monotonic()

    def on_failure(self) -> None:
        """Record a failed request."""
        self._total_failures += 1
        self._probe_in_flight = False
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


class PermanentError(Exception):
    """Raised on permanent failures that should NOT be retried.

    Includes: 4xx (auth / bad request), permission denied, quota exceeded.
    """
    pass


class TemporaryError(Exception):
    """Raised on transient failures that should be retried.

    Includes: 5xx, timeout, connection reset, rate limit.
    """
    pass


def classify_llm_error(exc: Exception) -> tuple[Exception, bool]:
    """Map a raw LLM / HTTP exception to the right error type + retry decision.

    Returns (typed_exception, should_retry).
    Uses duck-typing on status_code / response attributes so works with both
    OpenAI SDK errors and raw httpx errors.
    """
    if isinstance(exc, (CircuitOpenError, PermanentError, TemporaryError)):
        return (exc, not isinstance(exc, (CircuitOpenError, PermanentError)))

    # Extract status_code from common exception shapes
    status: int | None = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)

    if status is not None:
        if status == 429:  # rate limit
            return (TemporaryError(str(exc)), True)
        if 400 <= status < 500:
            return (PermanentError(f"4xx permanent error: {exc}"), False)
        if status >= 500:
            return (TemporaryError(f"5xx server error: {exc}"), True)

    # No status code — classify by exception type name
    name = type(exc).__name__.lower()
    if any(kw in name for kw in ("timeout", "connection", "network", "readerror")):
        return (TemporaryError(str(exc)), True)
    if any(kw in name for kw in ("authentication", "permission", "notfound", "badrequest", "invalid")):
        return (PermanentError(str(exc)), False)

    # Default: treat as temporary (conservative)
    return (TemporaryError(f"{type(exc).__name__}: {exc}"), True)


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


def jittered_backoff(attempt: int, base: float = 1.0) -> float:
    """Exponential backoff with jitter."""
    return base * (2 ** attempt) + random.uniform(0, 0.5)


# ------------------------------------------------------------------
# JSON extraction & parse helpers (企业级容错)
# ------------------------------------------------------------------

_JSON_FENCE_PATTERN = re.compile(
    r"(?:```(?:json)?\s*)?\s*(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}|\[(?:[^\[\]]|\[(?:[^\[\]]|\[[^\[\]]*\])*\])*\])\s*(?:```)?",
    re.DOTALL,
)


def robust_json_parse(text: str) -> dict | None:
    """Extract and parse JSON from LLM output, handling common formats.

    Handles:
    - Plain JSON: `{"key": "value"}`
    - Code-fenced: ````json\n{"key": "value"}\n``` `
    - Text-wrapped: `Sure! Here is the JSON:\n{"key": "value"}\nI hope this helps!`
    - <think> tags: `<think>...</think>{"key": "value"}`
    - Trailing commas: `{"key": "value",}`
    - BOM / leading whitespace / mixed prompts

    Returns parsed dict on success, None on failure.
    """
    cleaned = text.strip().removeprefix("﻿")  # strip BOM
    cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL).strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON object from markdown / prose wrapper
    match = _JSON_FENCE_PATTERN.search(cleaned)
    if match:
        candidate = match.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Try fixing trailing commas before last closing brace
    for fix in (_fix_trailing_commas, _fix_single_quotes, _fix_unquoted_keys):
        try:
            result = fix(cleaned)
            if result is not None:
                return json.loads(result)
        except json.JSONDecodeError:
            continue

    return None


def _fix_trailing_commas(text: str) -> str | None:
    """Remove trailing commas before } or ]."""
    import re as _re
    return _re.sub(r",\s*([}\]])", r"\1", text)


def _fix_single_quotes(text: str) -> str | None:
    """Replace single quotes with double quotes for JSON keys/values.

    Only applies when the text looks like it uses single quotes as JSON delimiters.
    """
    # Rough heuristic: if there's 'key': value pattern, it's single-quote JSON
    if "'" not in text:
        return None
    # Simple approach: replace single quotes that are likely JSON delimiters
    # This is imperfect but handles most cases
    result = re.sub(r"'([^']*?)':", r'"\1":', text)
    result = re.sub(r":\s*'([^']*?)'", r': "\1"', result)
    return result


def _fix_unquoted_keys(text: str) -> str | None:
    """Quote unquoted JSON keys (like {key: 'value'} → {"key": "value"})."""
    import re as _re
    # Match patterns like `{key: ` or `,key: `
    if not _re.search(r"[{,]\s*[a-zA-Z_][a-zA-Z0-9_]*\s*:", text):
        return None
    return _re.sub(r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)", r'\1"\2"\3', text)


async def call_llm_with_retry(
    chat_fn: Callable,
    messages: list[dict],
    *,
    max_retries: int = 2,
    tag: str = "llm",
    **kwargs: Any,
) -> str:
    """Call an LLM chat function with error-type-aware retry.

    Retry policy:
      - CircuitOpenError → re-raise immediately (provider is down)
      - PermanentError  → re-raise immediately (4xx / auth / quota)
      - TemporaryError  → retry with exponential backoff
      - empty / too-short response → retry as TemporaryError
      - any other Exception → retry (conservative default)

    Args:
        chat_fn: Async function (messages, **kwargs) → str, e.g. minimax_client.chat
        messages: Messages list for the LLM
        max_retries: Max retries (default 2 = up to 3 total attempts)
        tag: Logging tag for identifying the caller
        **kwargs: Passed to chat_fn

    Returns:
        LLM response text (str)

    Raises:
        PermanentError: On permanent failure (do NOT retry).
        CircuitOpenError: On circuit breaker.
        TemporaryError: After all retries exhausted on transient failure.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            result = await chat_fn(messages, **kwargs)
            if result and len(result.strip()) > 2:  # >2 chars filters pure "{}" etc
                return result
            last_exc = TemporaryError(f"{tag}: LLM returned empty or nearly-empty response")
        except (CircuitOpenError, PermanentError):
            raise  # never retry
        except TemporaryError as e:
            last_exc = e
        except Exception as e:
            # Unknown error — treat as transient, log with type
            last_exc = TemporaryError(f"{tag}: {type(e).__name__}: {e}")
            logger.warning("%s: unexpected %s, will retry", tag, type(e).__name__)

        if attempt < max_retries:
            delay = jittered_backoff(attempt)
            logger.warning(
                "%s: attempt %d/%d failed — %s, retrying in %.1fs",
                tag, attempt + 1, max_retries + 1,
                last_exc, delay,
            )
            await asyncio.sleep(delay)

    raise last_exc if last_exc else TemporaryError(f"{tag}: 调用失败")
