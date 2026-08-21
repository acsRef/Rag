"""sse-disconnect-continue：客户端断开后，生产者后台跑完并落库 completed。

覆盖：断开→完整答案入库；同会话 409；/generating 状态翻转；/cancel 显式取消；
服务关停取消；get_history 排除 interrupted 半截回答。

fake 流用「门控」模式保证确定性：每个 token 后 await 一个测试内创建的
asyncio.Event——测试未放行前生产者必然仍挂在注册表里（不依赖时序）。
"""
import asyncio

import pytest

from app.api.chat import (
    _IN_FLIGHT,
    cancel_generation,
    conversation_generating,
    shutdown_in_flight_generations,
    stream_chat,
)
from app.config import settings
from app.models.schemas import ChatRequest
from app.store.db import Message, get_db_ctx

USER = {"id": "test-user", "permissions": ["chat"], "role_ids": [], "is_admin": False}

_TOKENS = ["后台", "生成", "完成", "尾部"]


def _gated_stream(gate: asyncio.Event):
    """每个 token 后阻塞在 gate——放行前生成未完成，放行后一路跑完。"""
    async def stream(*args, **kwargs):
        for tok in _TOKENS:
            yield tok
            await gate.wait()
    return stream


async def _consume_until_token(body) -> str:
    """消费到第一个 token 事件，返回 metadata 里的真实 conversation_id。

    get_or_create_conversation 在会话不存在时生成新 id——注册表 key 是
    真实 id 而非请求传入的 id（与生产一致：前端从 metadata 事件取真实 id）。
    """
    conv_id = None
    for _ in range(50):
        evt = await anext(body)
        if evt.startswith("event: metadata"):
            import json
            conv_id = json.loads(evt.split("data: ", 1)[1])["conversation_id"]
        if "event: token" in evt:
            if conv_id is None:
                raise AssertionError("未收到 metadata 事件")
            return conv_id
    raise AssertionError("50 个事件内未消费到 token 事件")


async def test_disconnect_background_completes(
        integration_db, fake_llm_stack, monkeypatch):
    from app.llm.chat import minimax_client
    gate = asyncio.Event()
    monkeypatch.setattr(minimax_client, "chat_stream", _gated_stream(gate))
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    response = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-bg"), current_user=USER)
    body = response.body_iterator
    conv_id = await _consume_until_token(body)

    task = _IN_FLIGHT.get(conv_id)
    assert task is not None and not task.done(), "消费到 token 时生产者应仍运行"

    await body.aclose()          # ↓ 消费者断开
    assert not task.done(), "断开后生产者不应被取消（后台跑完）"

    gate.set()                   # 放行：后台任务继续跑完
    await asyncio.wait_for(task, timeout=10)

    with get_db_ctx() as session:
        msgs = session.query(Message).filter(
            Message.conversation_id == conv_id).order_by(Message.id.asc()).all()
    assert [m.role for m in msgs] == ["user", "assistant"], "断开后应落库完整一问一答"
    assert msgs[-1].status == "completed", "后台跑完应落 completed"
    assert "完成" in (msgs[-1].content or ""), "应为完整回答而非半截"


async def test_same_conversation_409(integration_db, fake_llm_stack, monkeypatch):
    from fastapi import HTTPException

    from app.llm.chat import minimax_client
    gate = asyncio.Event()
    monkeypatch.setattr(minimax_client, "chat_stream", _gated_stream(gate))
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    resp1 = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-409"), current_user=USER)
    body1 = resp1.body_iterator
    conv_id = await _consume_until_token(body1)
    task = _IN_FLIGHT.get(conv_id)
    assert task is not None and not task.done()

    with pytest.raises(HTTPException) as ei:
        await stream_chat(
            ChatRequest(query="再问", conversation_id=conv_id), current_user=USER)
    assert ei.value.status_code == 409

    await body1.aclose()
    gate.set()
    await asyncio.wait_for(task, timeout=10)


async def test_generating_endpoint_flips(integration_db, fake_llm_stack, monkeypatch):
    from app.llm.chat import minimax_client
    gate = asyncio.Event()
    monkeypatch.setattr(minimax_client, "chat_stream", _gated_stream(gate))
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    resp = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-gen"), current_user=USER)
    body = resp.body_iterator
    conv_id = await _consume_until_token(body)
    task = _IN_FLIGHT.get(conv_id)

    assert conversation_generating(conv_id, current_user=USER)["generating"] is True

    await body.aclose()
    gate.set()
    await asyncio.wait_for(task, timeout=10)
    assert conversation_generating(conv_id, current_user=USER)["generating"] is False


async def test_cancel_stops_background(integration_db, fake_llm_stack, monkeypatch):
    from app.llm.chat import minimax_client
    gate = asyncio.Event()
    monkeypatch.setattr(minimax_client, "chat_stream", _gated_stream(gate))
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    resp = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-cancel"), current_user=USER)
    conv_id = await _consume_until_token(resp.body_iterator)
    task = _IN_FLIGHT.get(conv_id)
    assert task is not None and not task.done()

    res = await cancel_generation(conv_id, current_user=USER)
    assert res["cancelled"] is True
    assert conv_id not in _IN_FLIGHT, "取消后注册表应清空，可立即发新消息"

    with get_db_ctx() as session:
        msgs = session.query(Message).filter(
            Message.conversation_id == conv_id).order_by(Message.id.asc()).all()
    assert msgs and msgs[0].role == "user", "用户消息在取消后仍应保留"
    # 半截助手消息是否落库取决于取消点（yield 间 vs LLM 流内），不强制断言


async def test_shutdown_cancels_background(integration_db, fake_llm_stack, monkeypatch):
    from app.llm.chat import minimax_client
    gate = asyncio.Event()
    monkeypatch.setattr(minimax_client, "chat_stream", _gated_stream(gate))
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    resp = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-shutdown"), current_user=USER)
    conv_id = await _consume_until_token(resp.body_iterator)
    task = _IN_FLIGHT.get(conv_id)
    await resp.body_iterator.aclose()
    assert task is not None and not task.done()

    await shutdown_in_flight_generations()
    assert task.done(), "关停后后台任务应被取消并结束"
    assert conv_id not in _IN_FLIGHT, "注册表应清理"


async def test_interrupted_message_excluded_from_history(integration_db):
    from app.core.memory import conversation_memory
    conv_id = conversation_memory.get_or_create_conversation("conv-hist", "test-user")
    await conversation_memory.add_message(conv_id, "user", "问题一", user_id="test-user")
    await conversation_memory.add_message(
        conv_id, "assistant", "半截回答", status="interrupted", user_id="test-user")
    await conversation_memory.add_message(
        conv_id, "assistant", "完整回答", status="completed", user_id="test-user")
    history = conversation_memory.get_history(conv_id)
    contents = [m["content"] for m in history if m["role"] == "assistant"]
    assert contents == ["完整回答"], "interrupted 半截回答不应进入 LLM 上下文"