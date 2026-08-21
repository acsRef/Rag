"""记忆机制改造回归测试：窗口化历史 / 丢消息修复 / 异步摘要 / 标题 / 退避。"""
import asyncio
import time

import pytest

from app.config import settings
from app.core.memory import conversation_memory
from app.llm.chat import minimax_client

_SUMMARY_TEXT = "## 主题与结论\n测试摘要"

_TEST_USERS = ("hist-user", "empty-user", "loss-user", "async-user",
               "backoff-user", "title-user")


@pytest.fixture(autouse=True)
def _memory_users(integration_db):
    """conversations.user_id 有外键约束，先建好全部测试用户（只增不删）。"""
    from app.store.db import User, get_db_ctx

    with get_db_ctx() as session:
        for uid in _TEST_USERS:
            if not session.query(User).filter(User.id == uid).first():
                session.add(User(id=uid, username=uid, hashed_password="unused", is_active=True))
        session.commit()
    yield


@pytest.fixture
def fake_summary_llm(monkeypatch):
    """替换 minimax_client.chat，捕获摘要 prompt。"""
    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages[-1]["content"])
        return _SUMMARY_TEXT

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    return captured


async def _flush():
    await asyncio.sleep(0.1)   # 让 fire-and-forget 摘要任务跑完


async def test_history_windowed_and_chronological(integration_db, monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 60)   # 恰好 3 条 20-token 消息
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "hist-user")
    contents = ["h%d%s" % (i, "x" * 28) for i in range(6)]    # 每条 20 token
    for c in contents:
        await conversation_memory.add_message(conv, "user", c, user_id="hist-user")

    history = conversation_memory.get_history(conv)
    assert [m["content"] for m in history] == contents[-3:]   # 最新 3 条、时间顺序


async def test_history_excludes_empty_messages(integration_db):
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "empty-user")
    await conversation_memory.add_message(conv, "user", "有内容", user_id="empty-user")
    await conversation_memory.add_message(conv, "assistant", "", user_id="empty-user")

    history = conversation_memory.get_history(conv)
    assert [m["content"] for m in history] == ["有内容"]


async def test_slid_out_messages_are_never_lost(integration_db, monkeypatch, fake_summary_llm):
    """核心回归：T1 摘要时在窗口内的消息，滑出后必须进入增量摘要（旧时间戳水位会永久丢失）。"""
    monkeypatch.setattr(settings, "history_max_tokens", 60)      # 窗口 = 3 条
    monkeypatch.setattr(settings, "summary_trigger_tokens", 30)  # 窗口外 ≥2 条即触发
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "loss-user")

    def msg(i):
        return "L%d%s" % (i, "x" * 28)   # 每条 20 token，内容可辨识

    # 阶段 1：m0..m4 → 窗口 m2..m4，首次摘要覆盖 m0,m1
    for i in range(5):
        await conversation_memory.add_message(conv, "user", msg(i), user_id="loss-user")
        await _flush()
    assert len(fake_summary_llm) == 1
    assert msg(0) in fake_summary_llm[0] and msg(1) in fake_summary_llm[0]

    # 阶段 2：m5,m6 → m2,m3 滑出（二者在阶段 1 都在窗口内、未被摘要）
    for i in (5, 6):
        await conversation_memory.add_message(conv, "user", msg(i), user_id="loss-user")
        await _flush()
    assert len(fake_summary_llm) == 2, "滑出消息未触发增量摘要（丢消息 bug）"
    assert msg(2) in fake_summary_llm[1] and msg(3) in fake_summary_llm[1]

    # 水位推进到 m3：摘要为 fake 返回值
    assert conversation_memory.get_summary(conv) == _SUMMARY_TEXT


async def test_summarize_does_not_block_add_message(integration_db, monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 60)
    monkeypatch.setattr(settings, "summary_trigger_tokens", 30)
    finished = []

    async def slow_chat(messages, **kw):
        await asyncio.sleep(0.5)
        finished.append(1)
        return _SUMMARY_TEXT

    monkeypatch.setattr(minimax_client, "chat", slow_chat)
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "async-user")

    t0 = time.monotonic()
    for i in range(5):
        await conversation_memory.add_message(conv, "user", "A%d%s" % (i, "x" * 28),
                                              user_id="async-user")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.4, "add_message 仍在同步等待摘要（实测 %.2fs）" % elapsed
    await asyncio.sleep(0.8)
    assert finished, "后台摘要任务未执行"


async def test_summary_failure_backoff(integration_db, monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 60)
    monkeypatch.setattr(settings, "summary_trigger_tokens", 30)
    # 重试退避归零，避免测试真实等待（与 unit 层 no_backoff 同手法）
    import app.llm.base as llm_base
    monkeypatch.setattr(llm_base, "jittered_backoff", lambda attempt, base=1.0: 0.0)
    calls = []

    async def failing_chat(messages, **kw):
        calls.append(1)
        raise RuntimeError("llm down")

    monkeypatch.setattr(minimax_client, "chat", failing_chat)
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "backoff-user")
    for i in range(6):
        await conversation_memory.add_message(conv, "user", "B%d%s" % (i, "x" * 28),
                                              user_id="backoff-user")
        await _flush()
    # 首次触发：call_llm_with_retry(max_retries=1) 内部 2 次尝试；
    # 之后 6 条里的后续 add 全部被退避拦截，不再调用
    assert len(calls) == 2, "退避未按预期工作（实际 %d 次调用）" % len(calls)

    # 模拟退避过期 → 允许重试（再触发 1 次 = 再 2 次尝试）
    from app.core import memory as memory_mod
    count, _ = memory_mod._summary_failures[conv]
    memory_mod._summary_failures[conv] = (count, 0.0)
    await conversation_memory.add_message(conv, "user", "B6%s" % ("x" * 28),
                                          user_id="backoff-user")
    await _flush()
    assert len(calls) == 4


async def test_title_from_first_user_message(integration_db):
    from app.store.db import Conversation, get_db_ctx

    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "title-user")
    await conversation_memory.add_message(conv, "user", "2024年各区域销售额排名",
                                          user_id="title-user")
    with get_db_ctx() as session:
        title = session.query(Conversation).filter_by(conversation_id=conv).first().title
    assert title == "2024年各区域销售额排名"
