"""Document upload & status API with auth."""
from app.ingestion.pipeline import ingestion_pipeline
from app.store.db import get_db_ctx, Document
from app.config import settings

from app.models.schemas import DocumentUploadResponse, DocumentStatusResponse, DocumentListItem
from app.middleware.auth import get_current_user
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])


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


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(default="default"),
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
    resolved_id = _resolve_document_id(file.filename or "unknown", kb_id, document_id)

    result = await ingestion_pipeline.run(
        filename=file.filename or "unknown",
        content=content,
        kb_id=kb_id,
        user_id=current_user["id"],
        visibility=visibility,
        allowed_roles=allowed_roles,
        document_id=resolved_id,
    )
    return DocumentUploadResponse(**result)


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
                created_at=d.created_at,
            )
            for d in docs
        ]


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
        )
