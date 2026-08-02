"""锁定 app/core/memory.py 纯函数行为（不触 DB）。

_get_outside_window 只读消息对象的 content 属性，用 SimpleNamespace 打桩即可，
不需要真实 Message ORM 实例。
"""
from types import SimpleNamespace

from app.config import settings
from app.core.memory import _estimate_tokens, _get_outside_window


def _msg(content: str, role: str = "user") -> SimpleNamespace:
    return SimpleNamespace(content=content, role=role, created_at=None)


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_mixed_content():
    assert _estimate_tokens("a" * 30) == 20   # len / 1.5 向下取整
    assert _estimate_tokens("x") == 1          # 至少为 1


def test_outside_window_empty_when_all_within_budget(monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 1000)
    msgs = [_msg("c" * 30) for _ in range(3)]  # 每条 20 token
    assert _get_outside_window(msgs) == []


def test_outside_window_returns_overflowed_old_messages(monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 50)
    msgs = [_msg("m%d%s" % (i, "c" * 28)) for i in range(5)]  # 每条 20 token
    # 从新往旧累加：m4(20) m3(40) m2(60 > 50) → outside = msgs[:3]
    assert _get_outside_window(msgs) == msgs[:3]


def test_outside_window_includes_the_overflowing_message(monkeypatch):
    # 恰好撑爆预算的那条消息算"窗口外"——与 get_history 的排除语义保持一致
    monkeypatch.setattr(settings, "history_max_tokens", 40)
    msgs = [_msg("c" * 30) for _ in range(3)]  # 每条 20 token
    outside = _get_outside_window(msgs)
    assert len(outside) == 1                    # 倒数第三条撑爆，被纳入 outside
