"""锁定 app/core/prompt.py 行为：token 估算一致性与历史裁剪。"""
import pytest

from app.core.memory import _estimate_tokens
from app.core.prompt import _est, prompt_builder


def test_est_matches_memory_estimator():
    # 两处估算实现必须一致（目前是复制粘贴的巧合一致，本测试把它变成契约）
    for s in ("", "hello", "中文混合 mixed 123", "x" * 1000):
        assert _est(s) == _estimate_tokens(s)


def test_trim_history_keeps_summary_and_chronological_order_when_fits():
    history = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "第二条"},
    ]
    text, tokens = prompt_builder._trim_history(history, "旧摘要", 999999)
    assert "## 对话历史摘要" in text
    assert "旧摘要" in text
    assert text.index("第一条") < text.index("第二条")   # 时间顺序
    assert tokens > 0


def test_trim_history_keeps_chronological_order_when_trimming():
    history = [{"role": "user", "content": "msg-%d %s" % (i, "x" * 60)} for i in range(6)]
    text, _ = prompt_builder._trim_history(history, "", budget=120)
    # 按出现位置排序，才能捕获渲染顺序（range 序遍历永远有序，断言会恒真）
    present = sorted((m for i in range(6) for m in ["msg-%d" % i] if m in text), key=text.index)
    assert len(present) >= 2
    assert present == sorted(present)   # 期望：旧消息在前


# ── D7：单块巨型 chunk 截断兜底 ──────────────────────────

def _chunk(text: str, chunk_id: str = "c1"):
    from app.models.schemas import RetrievedChunk
    return RetrievedChunk(chunk_id=chunk_id, document_id="d1", text=text, score=0.5)


def test_trim_chunks_truncates_oversized_single_chunk():
    """单 chunk 巨型：裁到 len==1 仍超预算 → 文本截断而非整块丢弃（避免检索空结果）。"""
    big = "字" * 5000
    # budget 大到能容纳部分字符：max_chars ≈ budget*1.5-20，应有截断
    kept = prompt_builder._trim_chunks([_chunk(big)], token_budget=200)
    assert len(kept) == 1
    assert len(kept[0].text) < len(big)
    assert len(kept[0].text) <= int(200 * 1.5)


def test_trim_chunks_drops_oversized_when_no_chars_left():
    """budget 仅够 1 chunk 但内容超大且截断后 ≤ 0 字符：丢弃。"""
    big = "字" * 5000
    kept = prompt_builder._trim_chunks([_chunk(big)], token_budget=1)
    assert kept == []


def test_trim_chunks_drops_oversized_when_no_chars_left():
    """budget 仅够 1 chunk 但内容超大且截断后 ≤ 0 字符：丢弃。"""
    big = "字" * 5000
    kept = prompt_builder._trim_chunks([_chunk(big)], token_budget=1)
    assert kept == []


def test_trim_chunks_normal_flow_unchanged():
    """多个 chunk 都装得下：不变。"""
    a = _chunk("A", "a")
    b = _chunk("B", "b")
    kept = prompt_builder._trim_chunks([a, b], token_budget=10000)
    assert [c.chunk_id for c in kept] == ["a", "b"]
