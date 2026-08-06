"""embed_query_with_fallback：熔断/异常降级为零向量（BM25-only），纯向量模式返回 None。"""
from app.config import settings
from app.llm.base import CircuitOpenError


async def test_fallback_on_circuit_open(monkeypatch):
    from app.core import retrieval

    async def _boom(text, max_retries=1):
        raise CircuitOpenError("open")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _boom)
    monkeypatch.setattr(settings, "hybrid_search_enabled", True)
    emb, degraded = await retrieval.embed_query_with_fallback("q")
    assert degraded is True
    assert emb == [0.0] * settings.embedding_dimension


async def test_fallback_on_generic_error(monkeypatch):
    from app.core import retrieval

    async def _boom(text, max_retries=1):
        raise RuntimeError("429")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _boom)
    monkeypatch.setattr(settings, "hybrid_search_enabled", True)
    emb, degraded = await retrieval.embed_query_with_fallback("q")
    assert degraded is True
    assert emb == [0.0] * settings.embedding_dimension


async def test_generic_error_pure_vector_returns_none(monkeypatch):
    from app.core import retrieval

    async def _boom(text, max_retries=1):
        raise RuntimeError("429")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _boom)
    monkeypatch.setattr(settings, "hybrid_search_enabled", False)
    emb, degraded = await retrieval.embed_query_with_fallback("q")
    assert degraded is True
    assert emb is None


async def test_pure_vector_mode_returns_none(monkeypatch):
    from app.core import retrieval

    async def _boom(text, max_retries=1):
        raise CircuitOpenError("open")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _boom)
    monkeypatch.setattr(settings, "hybrid_search_enabled", False)
    emb, degraded = await retrieval.embed_query_with_fallback("q")
    assert degraded is True
    assert emb is None


async def test_happy_path(monkeypatch):
    from app.core import retrieval

    async def _ok(text, max_retries=1):
        return [0.1] * 4

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _ok)
    emb, degraded = await retrieval.embed_query_with_fallback("q")
    assert degraded is False
    assert emb == [0.1] * 4


class _FakeCtx:
    """最小 DiagContext 替身：只收集 track_error 调用。"""

    def __init__(self):
        self.errors = []

    def track_error(self, step, error_type, message, *, retried=0, degraded=False):
        self.errors.append((step, error_type, message, degraded))


async def test_circuit_open_records_diag_error_generic_does_not(monkeypatch):
    """重构前的诊断语义必须保留：熔断降级记 track_error('embedding', 'CircuitOpenError')，
    普通异常降级不记（只留 warning 日志）。"""
    from app.core import retrieval

    async def _circuit(text, max_retries=1):
        raise CircuitOpenError("open")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _circuit)
    monkeypatch.setattr(settings, "hybrid_search_enabled", True)
    ctx = _FakeCtx()
    await retrieval.embed_query_with_fallback("q", ctx)
    assert ctx.errors == [
        ("embedding", "CircuitOpenError", "embedding circuit breaker open, BM25-only", True)
    ]

    async def _generic(text, max_retries=1):
        raise RuntimeError("boom")

    monkeypatch.setattr(retrieval.sf_embedding, "embed", _generic)
    ctx2 = _FakeCtx()
    await retrieval.embed_query_with_fallback("q", ctx2)
    assert ctx2.errors == []
