"""设计审查 P0-4：get_messages 必须回传 thinking_content。

messages.thinking_content 已入库，但 get_messages 之前不返回，前端历史重载
只能退回到解析内联 thinking 标签，thinking 内容丢失。这里锁定 API 返回值带
thinking_content 字段。
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(integration_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_get_messages_returns_thinking_content(client, admin_token, integration_db):
    from app.store.db import Conversation, Message, User, get_db_ctx, new_id, utc_now

    with get_db_ctx() as session:
        admin_id = session.query(User).filter(User.username == "admin").first().id
        conv_id = new_id()
        session.add(
            Conversation(
                conversation_id=conv_id,
                user_id=admin_id,
                title="thinking 测试",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.add(
            Message(
                message_id=new_id(),
                conversation_id=conv_id,
                user_id=admin_id,
                role="assistant",
                content="答案正文",
                thinking_content="思考过程：先检索再回答",
                created_at=utc_now(),
            )
        )
        session.commit()
        conv_under_test = conv_id

    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = client.get(
        f"/api/v1/chat/conversations/{conv_under_test}/messages",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == "答案正文"
    assert msgs[0]["thinking_content"] == "思考过程：先检索再回答"
