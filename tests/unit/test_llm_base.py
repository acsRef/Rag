"""锁定 app/llm/base.py 行为：熔断状态机 / 错误分类 / JSON 容错 / 退避 / 重试策略。"""

import pytest

import app.llm.base as base
from app.llm.base import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    PermanentError,
    TemporaryError,
    call_llm_with_retry,
    classify_llm_error,
    jittered_backoff,
    robust_json_parse,
)

# ── 熔断器 ──────────────────────────────────────────────


def test_breaker_closed_allows_requests():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
    assert cb.allow_request() is True
    assert cb.state == CircuitState.CLOSED


def test_breaker_opens_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
    for _ in range(3):
        cb.on_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


def test_breaker_half_open_after_cooldown(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0)
    cb.on_failure()
    assert cb.state == CircuitState.OPEN
    clock[0] = 31.0
    assert cb.allow_request() is True
    assert cb.state == CircuitState.HALF_OPEN


def test_breaker_probe_success_closes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0)
    cb.on_failure()
    clock[0] = 31.0
    cb.allow_request()  # 触发 OPEN → HALF_OPEN
    cb.on_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_breaker_probe_failure_reopens(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0)
    cb.on_failure()
    clock[0] = 31.0
    cb.allow_request()
    cb.on_failure()  # probe 失败
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


def test_breaker_half_open_allows_exactly_one_probe(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0)
    cb.on_failure()
    clock[0] = 31.0
    first = cb.allow_request()  # 转换调用
    second = cb.allow_request()  # 期望：总共只允许一个 probe
    assert first is True
    assert second is False


# ── 错误分类 ────────────────────────────────────────────


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"http {status_code}")


def test_classify_429_is_temporary_retryable():
    typed, retry = classify_llm_error(_FakeHTTPError(429))
    assert isinstance(typed, TemporaryError)
    assert retry is True


def test_classify_4xx_is_permanent_no_retry():
    typed, retry = classify_llm_error(_FakeHTTPError(401))
    assert isinstance(typed, PermanentError)
    assert retry is False


def test_classify_5xx_is_temporary_retryable():
    typed, retry = classify_llm_error(_FakeHTTPError(503))
    assert isinstance(typed, TemporaryError)
    assert retry is True


def test_classify_unknown_exception_defaults_to_temporary():
    typed, retry = classify_llm_error(ValueError("boom"))
    assert isinstance(typed, TemporaryError)
    assert retry is True


def test_classify_circuit_open_is_not_retried():
    exc = CircuitOpenError("open")
    typed, retry = classify_llm_error(exc)
    assert typed is exc
    assert retry is False


# ── JSON 容错解析 ───────────────────────────────────────


def test_robust_json_plain_object():
    assert robust_json_parse('{"a": 1}') == {"a": 1}


def test_robust_json_code_fenced():
    assert robust_json_parse('```json\n{"a": 1}\n```') == {"a": 1}


def test_robust_json_think_wrapped():
    raw = "<think>blah" + chr(10) + "</think>" + chr(10) * 2 + '{"a": 1}'
    assert robust_json_parse(raw) == {"a": 1}


def test_robust_json_trailing_comma():
    assert robust_json_parse('{"a": 1,}') == {"a": 1}


def test_robust_json_prose_wrapped():
    assert robust_json_parse('Sure! {"a": 1} done') == {"a": 1}


def test_robust_json_nested_object():
    assert robust_json_parse('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_robust_json_garbage_returns_none():
    assert robust_json_parse("no json here") is None


# ── 退避 ────────────────────────────────────────────────


def test_jittered_backoff_bounds():
    for attempt, lo in ((0, 1.0), (1, 2.0), (3, 8.0)):
        v = jittered_backoff(attempt)
        assert lo <= v < lo + 0.5


# ── 重试策略 ────────────────────────────────────────────


@pytest.fixture
def no_backoff(monkeypatch):
    """把退避 sleep 归零，避免重试测试真实等待。"""
    monkeypatch.setattr(base, "jittered_backoff", lambda attempt, base=1.0: 0.0)


async def test_retry_returns_first_success(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        return "okay"

    result = await call_llm_with_retry(chat_fn, [], tag="t")
    assert result == "okay"
    assert len(calls) == 1


async def test_retry_recovers_from_temporary_error(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise TemporaryError("flaky")
        return "okay"

    result = await call_llm_with_retry(chat_fn, [], tag="t")
    assert result == "okay"
    assert len(calls) == 2


async def test_retry_permanent_error_not_retried(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        raise PermanentError("auth")

    with pytest.raises(PermanentError):
        await call_llm_with_retry(chat_fn, [], tag="t")
    assert len(calls) == 1


async def test_retry_circuit_open_reraised(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        raise CircuitOpenError("open")

    with pytest.raises(CircuitOpenError):
        await call_llm_with_retry(chat_fn, [], tag="t")
    assert len(calls) == 1


async def test_retry_empty_response_treated_as_temporary(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        return ""

    with pytest.raises(TemporaryError):
        await call_llm_with_retry(chat_fn, [], tag="t", max_retries=1)
    assert len(calls) == 2
