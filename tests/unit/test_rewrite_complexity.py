"""锁定 rewrite 的复杂度分流：复杂查询动用推理模型（R1），简单查询走默认 V3。

背景：意图路由已回退 V3（第一层轻量分类），R1 改作「复杂查询规划」第二层。
本测试锁定 _is_complex_query 的判据，并验证 rewrite 按复杂/简单选择 model。
"""

from app.config import settings
from app.core.rewrite import _is_complex_query

# ── _is_complex_query 判据 ─────────────────────────────────


def test_simple_single_fact_is_not_complex():
    assert _is_complex_query("2023年营业收入是多少？") is False
    assert _is_complex_query("专利申请多少件？") is False
    assert _is_complex_query("什么是 RAG") is False


def test_reasoning_keywords_mark_complex():
    assert _is_complex_query("为什么研发投入连续三年加大？") is True
    assert _is_complex_query("判断盈利改善是否靠国内爆发") is True
    assert _is_complex_query("近三年海外收入趋势如何？") is True
    assert _is_complex_query("解释一下原因") is True


def test_year_range_marks_complex():
    assert _is_complex_query("2023-2025年营收分别是多少？") is True
    assert _is_complex_query("近三年营收对比") is True


def test_multi_entity_compare_marks_complex():
    assert _is_complex_query("A 和 B 有什么区别") is True
    assert _is_complex_query("混凝土机械和起重机械收入对比") is True


def test_empty_or_blank_not_complex():
    assert _is_complex_query("") is False
    assert _is_complex_query("   ") is False


# ── rewrite 模型选择 ───────────────────────────────────────


async def test_rewrite_uses_r1_for_complex(monkeypatch):
    from app.core.rewrite import query_rewrite_service
    from app.llm.chat import minimax_client

    seen = {}

    async def fake_chat(messages, **kw):
        seen["kw"] = kw
        return (
            '{"rewritten_query": "2023-2025年营收", '
            '"sub_questions": ["2023年营收", "2024年营收", "2025年营收"], '
            '"sub_dependencies": [[], [], []], "complexity": "complex"}'
        )

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    res = await query_rewrite_service.rewrite("2023-2025年营收分别是多少？", [], "")
    assert seen["kw"].get("model") == settings.rewrite_model
    assert settings.rewrite_model == "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"
    assert len(res.sub_questions) == 3


async def test_rewrite_uses_default_v3_for_simple(monkeypatch):
    from app.core.rewrite import query_rewrite_service
    from app.llm.chat import minimax_client

    seen = {}

    async def fake_chat(messages, **kw):
        seen["kw"] = kw
        return '{"rewritten_query": "2023年营收", "sub_questions": ["2023年营收"], "sub_dependencies": [[]], "complexity": "simple"}'

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    res = await query_rewrite_service.rewrite("2023年营收是多少？", [], "")
    assert seen["kw"].get("model") in (None, settings.chat_model)
    assert res.sub_questions == ["2023年营收"]
