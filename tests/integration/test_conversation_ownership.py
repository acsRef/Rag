"""会话 IDOR 回归测试：不得凭 conversation_id 触碰他人会话。"""
import asyncio

import pytest

from app.core.memory import conversation_memory


async def _to_thread(fn, *args):
    return await asyncio.to_thread(fn, *args)


@pytest.fixture(autouse=True)
def _ownership_users(integration_db):
    """conversations.user_id 有外键约束，先建好三个测试用户。"""
    from app.store.db import User, get_db_ctx

    # 只增不删：会话行外键引用着这些用户，删了会连累其他用例
    with get_db_ctx() as session:
        for uid in ("user-a", "user-b", "user-c"):
            if not session.query(User).filter(User.id == uid).first():
                session.add(User(id=uid, username=uid, hashed_password="unused", is_active=True))
        session.commit()
    yield


async def test_cannot_hijack_other_users_conversation(integration_db):
    conv_a = await _to_thread(conversation_memory.get_or_create_conversation, None, "user-a")
    await conversation_memory.add_message(conv_a, "user", "A 的秘密问题", user_id="user-a")

    # 用户 B 拿着 A 的 conversation_id 进来
    conv_b = await _to_thread(conversation_memory.get_or_create_conversation, conv_a, "user-b")

    assert conv_b != conv_a                      # 应为 B 新建的会话
    assert conversation_memory.get_history(conv_b) == []
    # A 的历史原样保留、未被 B 写入污染
    history_a = conversation_memory.get_history(conv_a)
    assert [m["content"] for m in history_a] == ["A 的秘密问题"]


async def test_owner_can_resume_own_conversation(integration_db):
    conv = await _to_thread(conversation_memory.get_or_create_conversation, None, "user-c")
    same = await _to_thread(conversation_memory.get_or_create_conversation, conv, "user-c")
    assert same == conv
