"""意图路由：prompt 必须含 KB 名称，LLM 才可能语义路由。"""


def test_intent_prompt_contains_kb_names(integration_db, monkeypatch):
    from app.core.intent import intent_classifier
    from app.llm.chat import minimax_client

    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages[-1]["content"])
        return '{"intent_type": "KB", "matches": []}'

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    import asyncio
    asyncio.run(intent_classifier.classify("什么是 Transformer", ["test-kb"]))
    assert captured
    assert "测试知识库" in captured[0], "意图 prompt 里没有 KB 名称，LLM 无法语义路由"
