"""锁定 app/core/memory.py 纯函数行为（不触 DB）。

_get_outside_window 已随 memory-overhaul 移除（窗口边界改为
_maybe_summarize 内联按 id 计算），相应用例一并删除。
"""
from app.core.memory import _estimate_tokens


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_mixed_content():
    assert _estimate_tokens("a" * 30) == 20   # len / 1.5 向下取整
    assert _estimate_tokens("x") == 1          # 至少为 1
