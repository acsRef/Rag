"""Chat API with optional auth."""
import asyncio
import logging

from app.core.pipeline import rag_pipeline
from app.core.diagnostics import DiagContext
from app.config import settings
from app.core.memory import conversation_memory
from app.models.schemas import ChatRequest, ConversationResponse
from app.middleware.auth import get_current_user
from app.store.db import get_db_ctx, Conversation
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])

logger = logging.getLogger(__name__)

# 进程内后台生成任务注册表：conversation_id → 生产者 task。
# 客户端断开后生产者继续跑完 pipeline 并落库 completed（见 sse-disconnect-continue plan）。
# 注意：进程内注册表，多 uvicorn worker 下仅覆盖发起请求的 worker（既有架构边界）。
_IN_FLIGHT: dict[str, asyncio.Task] = {}

_SSE_QUEUE_MAX = 256          # SSE 事件队列上限：慢客户端背压；断开后清空解除
_STREAM_END = object()        # 传输层结束哨兵（区别于任何 SSE 事件字符串）


def _release_in_flight(conv_id: str, task: asyncio.Task) -> None:
    """从注册表移除任务：确认还是自己再删，避免旧任务收尾误删新请求的任务。"""
    if _IN_FLIGHT.get(conv_id) is task:
        _IN_FLIGHT.pop(conv_id, None)


def _log_producer_error(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("后台生成任务异常（断开后链路）", exc_info=task.exception())


async def shutdown_in_flight_generations() -> None:
    """服务关停：取消所有后台生成任务。

    生产者捕获 CancelledError 后主动 aclose 生成器，触发 pipeline 的
    GeneratorExit 兜底——半截答案落 interrupted，不丢状态。
    """
    tasks = list(_IN_FLIGHT.values())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _build_diag_ctx(query: str):
    """设计审查 P0-1：diagnostics_enabled 关闭时不为 chat 流建诊断上下文。

    抽成小函数以便离线单测（stream_chat 是 async，体要 await 才执行）。
    """
    return DiagContext(query=query) if settings.diagnostics_enabled else None


@router.post("/stream")
async def stream_chat(
    req: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    from fastapi.responses import StreamingResponse
    if "chat" not in current_user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    user_id = current_user["id"]
    user_role_ids = current_user["role_ids"]
    can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
    ctx = _build_diag_ctx(req.query)

    # 幂等拿 conv_id（pipeline:128 还会再取一次）——注册表 key 与 409 检查需要它。
    # 注意：新会话（id 为 null/不存在）时 get_or_create 会生成新 id，必须回写
    # req.conversation_id，否则 pipeline 内第二次调用会再建一个会话（双会话 bug）。
    conv_id = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation,
        req.conversation_id, user_id,
    )
    req.conversation_id = conv_id

    # 同会话后台生成中 → 409（安全网；正常前端已禁发）
    active = _IN_FLIGHT.get(conv_id)
    if active is not None and not active.done():
        raise HTTPException(status_code=409, detail="该对话仍在生成回答，请稍后再试")

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_SSE_QUEUE_MAX)
    connected = {"value": True}

    async def producer():
        gen = rag_pipeline.execute(
            req, user_id=user_id, user_role_ids=user_role_ids,
            can_read_all=can_read_all, ctx=ctx,
        )
        try:
            async for evt in gen:
                if not connected["value"]:
                    continue  # 客户端已断开：丢弃模式，pipeline 继续跑完
                await queue.put(evt)
        except asyncio.CancelledError:
            # 服务关停/显式取消：主动关闭生成器，触发 GeneratorExit 兜底（落 interrupted）
            await gen.aclose()
            raise
        finally:
            try:
                if connected["value"]:
                    # 正常跑完：通知消费者收尾（1s 上限防关停时满队列阻塞）
                    await asyncio.wait_for(queue.put(_STREAM_END), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass  # 消费者已断开/关停：无需送达结束哨兵
            finally:
                _release_in_flight(conv_id, asyncio.current_task())

    task = asyncio.create_task(producer())
    _IN_FLIGHT[conv_id] = task
    task.add_done_callback(_log_producer_error)

    async def event_stream():
        try:
            while True:
                evt = await queue.get()
                if evt is _STREAM_END:
                    break
                yield evt
        finally:
            # 客户端断开：生产者不取消，切丢弃模式继续跑完
            connected["value"] = False
            # 清空队列，唤醒可能阻塞在 put 的生产者（解除背压）
            while not queue.empty():
                queue.get_nowait()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(
    current_user: dict = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0,
):
    limit = min(max(1, limit), 200)
    offset = max(0, offset)
    with get_db_ctx() as session:
        convs = (
            session.query(Conversation)
            .filter(Conversation.user_id == current_user["id"])
            .order_by(Conversation.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            ConversationResponse(
                conversation_id=c.conversation_id,
                title=c.title or "New conversation",
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in convs
        ]


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, current_user: dict = Depends(get_current_user)):
    from app.store.db import Message
    with get_db_ctx() as session:
        conv = session.query(Conversation).filter(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == current_user["id"],
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        session.query(Message).filter(Message.conversation_id == conversation_id).delete()
        session.delete(conv)
        session.commit()
        return {"ok": True}


@router.get("/conversations/{conversation_id}/generating")
def conversation_generating(conversation_id: str, current_user: dict = Depends(get_current_user)):
    """前端有边界轮询：后台生成任务是否仍在运行（归属鉴权与 get_messages 一致）。"""
    with get_db_ctx() as session:
        conv = session.query(Conversation).filter(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == current_user["id"],
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    task = _IN_FLIGHT.get(conversation_id)
    return {"generating": task is not None and not task.done()}


@router.post("/conversations/{conversation_id}/cancel")
async def cancel_generation(conversation_id: str, current_user: dict = Depends(get_current_user)):
    """「停止」按钮：显式取消生成任务。

    与「离开自动后台跑完」不同——用户主动停止 = 放弃该答案。
    取消后等任务清理完成（注册表移除），返回即可立即发新消息；
    半截助手消息是否落库取决于取消点（yield 间走 GeneratorExit 落 interrupted，
    LLM 流内 CancelledError 直穿不落），用户消息始终保留。
    """
    with get_db_ctx() as session:
        conv = session.query(Conversation).filter(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == current_user["id"],
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    task = _IN_FLIGHT.get(conversation_id)
    if task is None or task.done():
        return {"cancelled": False}
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return {"cancelled": True}


@router.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str, current_user: dict = Depends(get_current_user)):
    from app.store.db import Message

    with get_db_ctx() as session:
        conv = session.query(Conversation).filter(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == current_user["id"],
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        msgs = (
            session.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())   # 单调序，与 memory 模块约定一致
            .all()
        )
        return [
            {
                "role": m.role,
                "content": m.content,
                "thinking_content": m.thinking_content,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in msgs
        ]