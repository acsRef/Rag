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
    from app.store.db import get_db_ctx, User

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
