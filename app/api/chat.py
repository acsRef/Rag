"""Chat API with optional auth."""
from app.core.pipeline import rag_pipeline
from app.core.diagnostics import DiagContext
from app.config import settings
from app.models.schemas import ChatRequest, ConversationResponse
from app.middleware.auth import get_current_user
from app.store.db import get_db_ctx, Conversation
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


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
    return StreamingResponse(
        rag_pipeline.execute(req, user_id=user_id, user_role_ids=user_role_ids, can_read_all=can_read_all, ctx=ctx),
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
