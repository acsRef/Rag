"""锁定 app/core/rewrite.py：sub_questions 数量封顶（防并发雪崩）。"""

import json

from app.config import settings
from app.core.rewrite import query_rewrite_service
from app.llm.chat import minimax_client


class _Stub:
    """替 minimax_client.chat：返回 payload 序列化为 JSON 字符串（与真实 LLM 一致）。"""

    def __init__(self, payload):
        self.payload = payload

    async def __call__(self, messages, **kw):
        return json.dumps(self.payload, ensure_ascii=False)


async def test_sub_questions_capped(monkeypatch):
    monkeypatch.setattr(settings, "max_sub_questions", 3)
    monkeypatch.setattr(
        minimax_client,
        "chat",
        _Stub({"rewritten_query": "Q", "sub_questions": ["a", "b", "c", "d", "e"]}),
    )
    out = await query_rewrite_service.rewrite("q", [], "")
    assert out.sub_questions == ["a", "b", "c"], (
        "LLM 控制的 sub_questions 数量无封顶会触发 gather 并发雪崩（rerank 无内置限流）"
    )


async def test_sub_questions_under_cap_pass_through(monkeypatch):
    monkeypatch.setattr(settings, "max_sub_questions", 4)
    monkeypatch.setattr(
        minimax_client,
        "chat",
        _Stub({"rewritten_query": "Q", "sub_questions": ["a", "b"]}),
    )
    out = await query_rewrite_service.rewrite("q", [], "")
    assert out.sub_questions == ["a", "b"]


async def test_sub_questions_falls_back_to_rewritten_query(monkeypatch):
    monkeypatch.setattr(settings, "max_sub_questions", 3)
    monkeypatch.setattr(
        minimax_client,
        "chat",
        _Stub({"rewritten_query": "Q", "sub_questions": []}),
    )
    out = await query_rewrite_service.rewrite("q", [], "")
    assert out.sub_questions == ["Q"]


async def test_sub_questions_filters_non_string_items(monkeypatch):
    monkeypatch.setattr(settings, "max_sub_questions", 5)
    monkeypatch.setattr(
        minimax_client,
        "chat",
        _Stub({"rewritten_query": "Q", "sub_questions": ["a", 42, None, "b", "  ", "c"]}),
    )
    out = await query_rewrite_service.rewrite("q", [], "")
    assert out.sub_questions == ["a", "b", "c"]
