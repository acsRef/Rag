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
