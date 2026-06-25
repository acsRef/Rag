"""Document upload & status API with auth."""
import asyncio
import json
import logging
import threading
from typing import AsyncIterator, Optional

from app.ingestion.pipeline import ingestion_pipeline
from app.store.db import get_db_ctx, Document, Chunk, DocRoleAccess, PiiAlert, PiiHold, new_id, utc_now
from app.config import settings

from app.models.schemas import DocumentUploadResponse, DocumentStatusResponse, DocumentListItem
from app.middleware.auth import get_current_user
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])


# ── SSE 文档进度事件总线(进程内 pub/sub) ───────────────────────
# 内存 list 存所有订阅者,每个订阅者持有一个 asyncio.Queue
# uvicorn 单 worker 够用,多 worker 需要 Redis 之类共享
_doc_event_subscribers: list[asyncio.Queue] = []
_subscribers_lock = asyncio.Lock()
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_main_loop_lock = threading.Lock()


def _resolve_sse_user(request: Request) -> dict | None:
    """Resolve user from Bearer header, falling back to ?token= query param.

    EventSource 无法设置 Authorization header,故支持 token query param。
    """
    from app.middleware.auth import decode_token
    from app.store.auth_store import get_user_by_id, get_user_role_ids, get_user_permissions

    auth = request.headers.get("Authorization")
    token_str: str | None = None
    if auth and auth.startswith("Bearer "):
        token_str = auth[7:]
    else:
        token_str = request.query_params.get("token")

    if not token_str:
        return None
    payload = decode_token(token_str)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user or not user.is_active:
        return None
    role_ids = get_user_role_ids(user.id)
    permissions = get_user_permissions(user.id)
    return {
        "id": user.id, "username": user.username,
        "display_name": user.display_name,
        "role_ids": role_ids,
        "permissions": permissions,
        "is_admin": "admin" in permissions,
    }


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """在 FastAPI startup 时调一次,保存主事件循环引用,供后台线程 emit。"""
    global _main_loop
    with _main_loop_lock:
        _main_loop = loop


async def subscribe_doc_events() -> asyncio.Queue:
    """新建一个订阅者,返回它的 Queue。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    async with _subscribers_lock:
        _doc_event_subscribers.append(q)
    return q


async def unsubscribe_doc_events(q: asyncio.Queue) -> None:
    async with _subscribers_lock:
        if q in _doc_event_subscribers:
            _doc_event_subscribers.remove(q)


def emit_doc_progress(event: dict) -> None:
    """同步 emit 一个事件(从后台 ingestion 线程调用)。

    由于后台任务在 `asyncio.to_thread` 里跑(同步),而订阅者在 async SSE handler 里,
    用 `loop.call_soon_threadsafe` 把 put_nowait 排到事件循环。
    如果主 loop 未设置或已关闭,静默 return(不影响 ingestion 主流程)。
    """
    with _main_loop_lock:
        loop = _main_loop
    if loop is None or loop.is_closed():
        return
    for q in list(_doc_event_subscribers):
        try:
            loop.call_soon_threadsafe(q.put_nowait, event)
        except Exception:
            pass


def _get_kb_visibility(kb_id: str) -> tuple[str, list[int]]:
    """Return (visibility, allowed_role_ids) for a KB."""
    from app.store.db import KnowledgeBase, KBRoleAccess
    with get_db_ctx() as session:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
        roles = session.query(KBRoleAccess.role_id).filter(KBRoleAccess.kb_id == kb_id).all()
        return kb.visibility, [r[0] for r in roles]


def _resolve_document_id(filename: str, kb_id: str, client_doc_id: str | None) -> str | None:
    """Determine document_id: explicit, matched by filename, or None for new.

    Returns None if no match found (new document).
    Raises HTTPException(409) on ambiguous match.
    """
    if client_doc_id:
        with get_db_ctx() as session:
            doc = session.query(Document).filter(
                Document.document_id == client_doc_id,
                Document.kb_id == kb_id,
            ).first()
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found in this KB")
            return client_doc_id

    with get_db_ctx() as session:
        matches = (
            session.query(Document)
            .filter(
                Document.filename == filename,
                Document.kb_id == kb_id,
            )
            .order_by(Document.created_at.desc())
            .all()
        )
        if len(matches) == 0:
            return None
        if len(matches) == 1:
            return matches[0].document_id
        conflict_ids = [m.document_id for m in matches]
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"知识库中存在多个同名文档 ({filename})，请选择具体文档后重新上传",
                "document_ids": conflict_ids,
            },
        )


def _ensure_document_id(document_id: str | None) -> str:
    return document_id or new_id()


def _upsert_processing_document(doc_id: str, filename: str, kb_id: str, user_id: str):
    with get_db_ctx() as session:
        existing = session.query(Document).filter(Document.document_id == doc_id).first()
        if existing:
            existing.status = "processing"
            existing.filename = filename
            existing.updated_at = utc_now()
        else:
            session.add(Document(
                document_id=doc_id, kb_id=kb_id, filename=filename,
                owner_id=user_id, status="processing", chunk_count=0,
                content_hash="", created_at=utc_now(), updated_at=utc_now(),
            ))
        session.commit()


async def _run_ingestion_background(
    filename: str, content: bytes, kb_id: str, user_id: str,
    visibility: str, allowed_roles: list[int], document_id: str,
):
    try:
        await ingestion_pipeline.run(
            filename=filename, content=content, kb_id=kb_id,
            user_id=user_id, visibility=visibility,
            allowed_roles=allowed_roles, document_id=document_id,
        )
    except Exception:
        logger.exception("Background ingestion failed for doc_id=%s", document_id)
        with get_db_ctx() as session:
            doc = session.query(Document).filter(Document.document_id == document_id).first()
            if doc:
                doc.status = "failed"
                session.commit()


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    kb_id: str = Form(...),
    document_id: str | None = Form(default=None),
    current_user: dict = Depends(get_current_user),
):
    can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
    if "doc.upload" not in current_user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    if not can_read_all:
        from app.store.db import KnowledgeBase
        with get_db_ctx() as session:
            kb = session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
            if not kb or kb.owner_id != current_user["id"]:
                raise HTTPException(status_code=403, detail="无权向该知识库上传文档")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content)//1024//1024}MB），最大允许 {settings.max_upload_size_mb}MB",
        )

    visibility, allowed_roles = _get_kb_visibility(kb_id)
    resolved_id = _ensure_document_id(_resolve_document_id(file.filename or "unknown", kb_id, document_id))
    _upsert_processing_document(resolved_id, file.filename or "unknown", kb_id, current_user["id"])

    background_tasks.add_task(
        _run_ingestion_background,
        filename=file.filename or "unknown",
        content=content,
        kb_id=kb_id,
        user_id=current_user["id"],
        visibility=visibility,
        allowed_roles=allowed_roles,
        document_id=resolved_id,
    )

    return DocumentUploadResponse(
        document_id=resolved_id,
        filename=file.filename or "unknown",
        status="processing",
        chunk_count=0,
    )


@router.get("", response_model=list[DocumentListItem])
async def list_documents(
    current_user: dict = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0,
):
    limit = min(max(1, limit), 200)
    offset = max(0, offset)
    with get_db_ctx() as session:
        can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
        query = session.query(Document)
        if not can_read_all:
            query = query.filter(Document.owner_id == current_user["id"])
        docs = query.order_by(Document.created_at.desc()).offset(offset).limit(limit).all()
        return [
            DocumentListItem(
                document_id=d.document_id,
                filename=d.filename,
                status=d.status,
                kb_id=d.kb_id,
                chunk_count=d.chunk_count,
                embedded_chunk_count=d.embedded_chunk_count or 0,
                error_message=d.error_message or "",
                created_at=d.created_at,
            )
            for d in docs
        ]


@router.delete("/{document_id}")
def delete_document(document_id: str, current_user: dict = Depends(get_current_user)):
    can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
    if "doc.delete" not in current_user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    with get_db_ctx() as session:
        doc = session.query(Document).filter(Document.document_id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if not can_read_all and doc.owner_id != current_user["id"]:
            raise HTTPException(status_code=403, detail="Access denied")

        session.query(Chunk).filter(Chunk.document_id == document_id).delete()
        session.query(DocRoleAccess).filter(DocRoleAccess.document_id == document_id).delete()
        session.query(PiiAlert).filter(
            PiiAlert.source_id == document_id, PiiAlert.source_type == "document"
        ).delete()
        session.query(PiiHold).filter(
            PiiHold.source_id == document_id, PiiHold.source_type == "document"
        ).delete()
        session.delete(doc)
        session.commit()

    return {"message": "Document deleted", "document_id": document_id}


@router.get("/events")
async def document_events(request: Request):
    """SSE 文档进度事件流。

    事件类型 `doc_progress`:
      data: {"document_id": "ab12cd34...", "embedded_chunk_count": 5,
             "chunk_count": 21, "status": "indexing"}

    认证: Bearer header 优先,回退到 `?token=` query param(适配 EventSource)。
    客户端用 EventSource 订阅即可。每完成一个 chunk 或最终状态变化时触发。
    """
    user = _resolve_sse_user(request)
    queue = await subscribe_doc_events()

    async def event_stream() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: doc_progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await unsubscribe_doc_events(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{document_id}", response_model=DocumentStatusResponse)
async def get_document(document_id: str, current_user: dict = Depends(get_current_user)):
    with get_db_ctx() as session:
        record = session.query(Document).filter_by(document_id=document_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="Document not found")
        can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
        if not can_read_all and record.owner_id != current_user["id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        return DocumentStatusResponse(
            document_id=record.document_id,
            filename=record.filename,
            status=record.status,
            chunk_count=record.chunk_count,
            embedded_chunk_count=record.embedded_chunk_count or 0,
            error_message=record.error_message or "",
        )
