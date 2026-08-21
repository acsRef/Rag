"""DB 降级链路端到端：DB-2 穿透兜底 + DB-3 空结果与故障分流 + DB-4 /health 探针。

DB-2：pipeline.py 的 DB 调用（list_kb_ids / get_history / get_summary / add_message）
    异常不再硬穿透 SSE 流——优雅降级 + logger 告警。
DB-3：检索空结果叠加 postgres 熔断 → 发 error 事件告知用户服务不可用并终止，
    不进 LLM 幻觉路径；正常空结果走原有 no_context。
DB-4：/health 探针反映 DB 状态（db: bool），DB 故障时 status=degraded。
"""

from fastapi.testclient import TestClient


def test_health_endpoint_reports_db_status(integration_db):
    """DB-4：/health 反映 DB 状态。集成环境下 PG 可达 → ok/db: true。"""
    from app.main import app

    with TestClient(app) as c:
        resp = c.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["db"] is True


def _open_postgres_breaker():
    """把 postgres 熔断器强制置 OPEN：模拟 DB 长时间不可用。"""
    from app.llm import base as llm_base
    from app.llm.base import CircuitState

    b = llm_base.provider_health.get("postgres")
    with b._lock:
        b.state = CircuitState.OPEN
    return b


def _close_postgres_breaker(b):
    from app.llm.base import CircuitState

    with b._lock:
        b.state = CircuitState.CLOSED
        b.failure_count = 0


def _close_postgres_breaker_safe():
    """try-close without exception（cleanup 用）。"""
    from app.llm import base as llm_base
    from app.llm.base import CircuitState

    if "postgres" in llm_base.provider_health._breakers:
        with llm_base.provider_health._breakers["postgres"]._lock:
            llm_base.provider_health._breakers["postgres"].state = CircuitState.CLOSED
            llm_base.provider_health._breakers["postgres"].failure_count = 0


async def _collect(req, **kwargs):
    """消费 rag_pipeline.execute 单例产出。"""
    from app.core.pipeline import rag_pipeline

    out = []
    async for e in rag_pipeline.execute(req, **kwargs):
        out.append(e)
    return "".join(out)


async def test_db_down_lists_kb_ids_does_not_crash(monkeypatch, fake_llm_stack):
    """DB-2：list_kb_ids 失败 → pipeline 不再穿透异常，按空 KB 列表继续。"""
    from app.models.schemas import ChatRequest
    from app.store import pgvector_store

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(pgvector_store, "list_kb_ids", boom)

    req = ChatRequest(query="hi")
    # 期望：异常被吞，流正常产出（最后 error/done），不抛
    out = await _collect(req, user_id="test-user")
    # 事件序列至少包含 done —— 流没有被打断
    assert "event: done" in out


async def test_retrieval_empty_with_db_open_emits_error_not_no_context(
    monkeypatch, integration_db, fake_llm_stack
):
    """DB-3：检索空 + postgres 熔断 OPEN → error 事件告知用户，不走 LLM。"""
    from app.core import retrieval as retrieval_mod
    from app.llm import chat as chat_mod
    from app.models.schemas import ChatRequest

    _open_postgres_breaker()
    try:
        # 检索返回空
        monkeypatch.setattr(retrieval_mod, "_search_kb", lambda *a, **kw: [])

        # LLM 若被调用说明测试失败——用计数器断言
        chat_calls = []

        async def chat_stub(messages, **kw):
            chat_calls.append("chat")
            return "不应该被调用"

        async def chat_stream_stub(messages, **kw):
            chat_calls.append("stream")
            yield "不应该被调用"
            return

        monkeypatch.setattr(chat_mod.minimax_client, "chat", chat_stub)
        monkeypatch.setattr(chat_mod.minimax_client, "chat_stream", chat_stream_stub)

        req = ChatRequest(query="查询")
        out = await _collect(req, user_id="test-user")

        assert "event: error" in out
        assert "知识库服务暂时不可用" in out
        assert "event: no_context" not in out, "故障分流：不应发 no_context 走 LLM 路径"
        assert chat_calls == [], f"LLM 不应被调用，实际调用 {len(chat_calls)} 次"
        assert "event: done" in out
    finally:
        _close_postgres_breaker_safe()


async def test_retrieval_empty_with_db_healthy_emits_no_context(
    monkeypatch, integration_db, ingest_docs, fake_llm_stack
):
    """DB 健康时空结果仍走 no_context（不进 LLM 也是 try—但保留 LLM 路径以触发 fallback）。

    校验：postgres 熔断 CLOSED + 检索空 → 发 no_context（不是 error）。
    """
    from app.core import retrieval as retrieval_mod
    from app.llm import chat as chat_mod
    from app.models.schemas import ChatRequest

    # stub chat_stream 让流走到正常结束（不抛错，否则通用 except 会发 error 事件）
    async def fake_stream(messages, **kw):
        yield "answer"
        return

    monkeypatch.setattr(chat_mod.minimax_client, "chat_stream", fake_stream)

    monkeypatch.setattr(retrieval_mod, "_search_kb", lambda *a, **kw: [])

    req = ChatRequest(query="查询")
    out = await _collect(req, user_id="test-user")

    assert "event: no_context" in out
    # 'event: error' must not appear as its OWN event line (it would appear inside
    # 'event: meta...data: {}' as substring, but not as an actual error event).
    # Split by event: marker to check actual events:
    event_starts = [i for i in range(len(out)) if out.startswith("event: ", i)]
    actual_events = []
    for i, start in enumerate(event_starts):
        end = event_starts[i + 1] if i + 1 < len(event_starts) else len(out)
        actual_events.append(out[start:end].split("\n", 1)[0])
    assert "event: error" not in actual_events, (
        f"DB 健康时不应触发 DB-3 的 error 事件，实际事件序列：{actual_events}"
    )
