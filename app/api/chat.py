from app.core.pipeline import rag_pipeline
from app.models.schemas import ChatRequest
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@router.post("/stream")
async def stream_chat(req: ChatRequest):
    return StreamingResponse(
        rag_pipeline.execute(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
