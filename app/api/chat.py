"""Chat API with optional auth."""
from app.core.pipeline import rag_pipeline
from app.core.memory import conversation_memory
from app.models.schemas import ChatRequest, ConversationResponse
from app.middleware.auth import get_current_user, get_optional_user
from app.store.db import get_session, Conversation
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@router.post("/stream")
async def stream_chat(
    req: ChatRequest,
    current_user: dict | None = Depends(get_optional_user),
):
    from fastapi.responses import StreamingResponse
    user_id = current_user["id"] if current_user else "anonymous"
    req.user_id = user_id
    return StreamingResponse(
        rag_pipeline.execute(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(current_user: dict = Depends(get_current_user)):
    session = get_session()
    try:
        convs = (
            session.query(Conversation)
            .filter(Conversation.user_id == current_user["id"])
            .order_by(Conversation.updated_at.desc())
            .limit(50)
            .all()
        )
        return [
            ConversationResponse(
                conversation_id=c.conversation_id,
                title=c.title or "New conversation",
                created_at=c.created_at.isoformat() if c.created_at else "",
                updated_at=c.updated_at.isoformat() if c.updated_at else "",
            )
            for c in convs
        ]
    finally:
        session.close()


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, current_user: dict = Depends(get_current_user)):
    from app.store.db import Message
    session = get_session()
    try:
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
    finally:
        session.close()
