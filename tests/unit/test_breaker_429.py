"""锁定 429/4xx 不得触发熔断（AGENTS §8）+ embed/chat 各路径守卫一致。

classify_llm_error 旧实现把 429 归类为 TemporaryError，导致调用方
`if not isinstance(typed, PermanentError): _on_failure()` 在限流时也会计失败，
把 embedding 服务打穿成 outage。修复：429 → RateLimitError（TemporaryError
子类，可重试但不计熔断失败）；其他 4xx 同理不计失败。
"""
import asyncio

import pytest

from app.llm import base as llm_base
from app.llm import chat as chat_mod
from app.llm import embedding as emb_mod


class _FakeAPIError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(message)


def _fresh_breakers():
    """每个用例前重置 provider_health 计数器，避免跨用例串扰。"""
    llm_base.provider_health._breakers.clear()


# ── classify_llm_error 类型语义 ─────────────────────────

def test_429_returns_rate_limit_error():
    typed, should_retry = llm_base.classify_llm_error(_FakeAPIError(429))
    assert isinstance(typed, llm_base.RateLimitError)
    assert isinstance(typed, llm_base.TemporaryError)  # 子类可重试
    assert should_retry is True


def test_4xx_other_returns_permanent():
    typed, should_retry = llm_base.classify_llm_error(_FakeAPIError(400))
    assert isinstance(typed, llm_base.PermanentError)
    assert should_retry is False


def test_5xx_returns_temporary():
    typed, should_retry = llm_base.classify_llm_error(_FakeAPIError(503))
    assert isinstance(typed, llm_base.TemporaryError)
    assert not isinstance(typed, llm_base.RateLimitError)
    assert should_retry is True


# ── SFEmbedding.embed() ────────────────────────────────────

async def test_embed_429_does_not_trip_breaker(monkeypatch):
    _fresh_breakers()
    sf = emb_mod.sf_embedding

    class _FakeEmbeddings:
        @staticmethod
        async def create(*a, **kw):
            raise _FakeAPIError(429, "rate limited")

    class _FakeClient:
        embeddings = _FakeEmbeddings

    sf._client = _FakeClient()
    sf._client_loop_id = id(asyncio.get_event_loop())

    with pytest.raises(llm_base.RateLimitError):
        await sf.embed("test")

    breaker = llm_base.provider_health.get(sf.provider)
    assert breaker.failure_count == 0, "429 不得计为熔断失败"


async def test_embed_5xx_trips_breaker(monkeypatch):
    _fresh_breakers()
    sf = emb_mod.sf_embedding

    class _FakeEmbeddings:
        @staticmethod
        async def create(*a, **kw):
            raise _FakeAPIError(503, "down")

    class _FakeClient:
        embeddings = _FakeEmbeddings

    sf._client = _FakeClient()
    sf._client_loop_id = id(asyncio.get_event_loop())
    # 跳过退避加快测试
    monkeypatch.setattr(emb_mod, "jittered_backoff", lambda a, base=1.0: 0.0)

    with pytest.raises(llm_base.TemporaryError):
        await sf.embed("test")

    breaker = llm_base.provider_health.get(sf.provider)
    # embed() 默认 max_retries=1：2 次尝试 → 2 次 on_failure
    assert breaker.failure_count >= 1, "5xx 必须计为熔断失败"


async def test_embed_400_does_not_trip_breaker(monkeypatch):
    _fresh_breakers()
    sf = emb_mod.sf_embedding

    class _FakeEmbeddings:
        @staticmethod
        async def create(*a, **kw):
            raise _FakeAPIError(400, "bad request")

    class _FakeClient:
        embeddings = _FakeEmbeddings

    sf._client = _FakeClient()
    sf._client_loop_id = id(asyncio.get_event_loop())

    with pytest.raises(llm_base.PermanentError):
        await sf.embed("test")

    breaker = llm_base.provider_health.get(sf.provider)
    assert breaker.failure_count == 0


# ── SFEmbedding.embed_single_chunk 与 _try_batch_with_retry ───────

async def test_embed_single_chunk_429_retries_then_no_failure(monkeypatch):
    _fresh_breakers()
    sf = emb_mod.sf_embedding

    async def raise_429(*a, **kw):
        raise llm_base.RateLimitError("rate limited")

    class _FakeClient:
        embeddings = type(
            "E", (), {"create": staticmethod(raise_429)})()

    sf._client = _FakeClient()
    sf._client_loop_id = id(asyncio.get_event_loop())
    monkeypatch.setattr(emb_mod, "_jittered_sleep",
                        lambda s: asyncio.sleep(0))
    sf._check_breaker()

    emb, err = await sf.embed_single_chunk("text")
    assert emb is None and err
    breaker = llm_base.provider_health.get(sf.provider)
    assert breaker.failure_count == 0, "429 耗尽重试后仍不得计熔断失败"


async def test_try_batch_429_no_failure(monkeypatch):
    _fresh_breakers()
    sf = emb_mod.sf_embedding

    async def raise_429(*a, **kw):
        raise llm_base.RateLimitError("rate limited")

    class _FakeClient:
        embeddings = type(
            "E", (), {"create": staticmethod(raise_429)})()

    sf._client = _FakeClient()
    sf._client_loop_id = id(asyncio.get_event_loop())
    monkeypatch.setattr(emb_mod, "_jittered_sleep",
                        lambda s: asyncio.sleep(0))

    result = await emb_mod._try_batch_with_retry(sf, ["a"])
    assert result is None
    breaker = llm_base.provider_health.get(sf.provider)
    assert breaker.failure_count == 0


# ── MiniMaxClient ──────────────────────────────────────────

async def test_chat_429_does_not_trip_breaker(monkeypatch):
    _fresh_breakers()
    client = chat_mod.minimax_client

    class _FakeCompletions:
        @staticmethod
        async def create(*a, **kw):
            raise _FakeAPIError(429)

    class _FakeChat:
        completions = _FakeCompletions

    class _FakeClient:
        chat = _FakeChat

    client._client = _FakeClient()
    client._client_loop_id = id(asyncio.get_event_loop())

    with pytest.raises(llm_base.RateLimitError):
        await client.chat([{"role": "user", "content": "hi"}])
    breaker = llm_base.provider_health.get(client.provider)
    assert breaker.failure_count == 0


async def test_chat_stream_429_does_not_trip_breaker(monkeypatch):
    _fresh_breakers()
    client = chat_mod.minimax_client

    class _FakeCompletions:
        @staticmethod
        async def create(*a, **kw):
            raise _FakeAPIError(429)

    class _FakeChat:
        completions = _FakeCompletions

    class _FakeClient:
        chat = _FakeChat

    client._client = _FakeClient()
    client._client_loop_id = id(asyncio.get_event_loop())

    with pytest.raises(llm_base.RateLimitError):
        async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass
    breaker = llm_base.provider_health.get(client.provider)
    assert breaker.failure_count == 0


async def test_chat_5xx_trips_breaker(monkeypatch):
    _fresh_breakers()
    client = chat_mod.minimax_client

    class _FakeCompletions:
        @staticmethod
        async def create(*a, **kw):
            raise _FakeAPIError(503)

    class _FakeChat:
        completions = _FakeCompletions

    class _FakeClient:
        chat = _FakeChat

    client._client = _FakeClient()
    client._client_loop_id = id(asyncio.get_event_loop())
    monkeypatch.setattr(llm_base, "jittered_backoff", lambda a, base=1.0: 0.0)

    with pytest.raises(llm_base.TemporaryError):
        await client.chat([{"role": "user", "content": "hi"}])
    breaker = llm_base.provider_health.get(client.provider)
    assert breaker.failure_count >= 1