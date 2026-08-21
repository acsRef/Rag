"""熔断打开时的对话链路：兜底文案必须流式给用户且持久化。

旧实现捕获 CircuitOpenError 后只把兜底文案写库、不 yield 任何 token——
用户当轮看到空白，刷新后才见文案。
"""


async def test_circuit_open_streams_degraded_reply(
    integration_db, fake_llm_stack, ingest_docs, monkeypatch
):
    from app.core.pipeline import rag_pipeline
    from app.llm.base import CircuitOpenError
    from app.llm.chat import minimax_client
    from app.models.schemas import ChatRequest
    from app.store.db import Message, get_db_ctx

    async def open_circuit(*args, **kwargs):
        raise CircuitOpenError("breaker open")
        yield  # 使本函数成为 async generator（与真实 chat_stream 签名一致）

    monkeypatch.setattr(minimax_client, "chat_stream", open_circuit)

    events = []
    async for raw in rag_pipeline.execute(ChatRequest(query="什么是 RAG"), user_id="test-user"):
        events.append(raw)

    joined = "".join(events)
    assert "event: token" in joined, "熔断打开时必须向用户流式兜底文案"
    assert "AI 服务暂时不可用" in joined
    assert "event: done" in joined

    # 兜底文案同样要持久化（供历史会话加载）
    with get_db_ctx() as session:
        msgs = (
            session.query(Message)
            .filter(Message.role == "assistant")
            .order_by(Message.id.desc())
            .all()
        )
    assert any("AI 服务暂时不可用" in (m.content or "") for m in msgs), "熔断兜底文案未入库"
