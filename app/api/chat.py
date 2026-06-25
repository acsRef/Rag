"""Chat API with optional auth."""
from app.core.pipeline import rag_pipeline
from app.core.memory import conversation_memory
from app.models.schemas import ChatRequest, ConversationResponse
from app.middleware.auth import get_current_user, get_optional_user
from app.store.db import get_db_ctx, Conversation
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@router.post("/stream")
async def stream_chat(
    req: ChatRequest,
    current_user: dict | None = Depends(get_optional_user),
):
    from fastapi.responses import StreamingResponse
    user_id = current_user["id"] if current_user else "anonymous"
    user_role_ids = current_user.get("role_ids") if current_user else None
    can_read_all = bool(current_user and (current_user["is_admin"] or "doc.read_all" in current_user["permissions"]))
    return StreamingResponse(
        rag_pipeline.execute(req, user_id=user_id, user_role_ids=user_role_ids, can_read_all=can_read_all),
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
            .order_by(Message.created_at.asc())
            .all()
        )
        return [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in msgs
        ]
