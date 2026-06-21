"""Document upload & status API with auth."""
from app.ingestion.pipeline import ingestion_pipeline
from app.store.db import get_session, Document

from app.models.schemas import DocumentUploadResponse, DocumentStatusResponse, DocumentListItem
from app.middleware.auth import get_current_user
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])


def _get_kb_visibility(kb_id: str) -> tuple[str, list[int]]:
    """Return (visibility, allowed_role_ids) for a KB."""
    from app.store.db import KnowledgeBase, KBRoleAccess
    session = get_session()
    try:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            return "public", []
        roles = session.query(KBRoleAccess.role_id).filter(KBRoleAccess.kb_id == kb_id).all()
        return kb.visibility, [r[0] for r in roles]
    finally:
        session.close()


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(default="default"),
    current_user: dict = Depends(get_current_user),
):
    if "doc.upload" not in current_user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    content = await file.read()
    visibility, allowed_roles = _get_kb_visibility(kb_id)

    result = ingestion_pipeline.run(
        filename=file.filename or "unknown",
        content=content,
        kb_id=kb_id,
        user_id=current_user["id"],
        visibility=visibility,
        allowed_roles=allowed_roles,
    )
    return DocumentUploadResponse(**result)


@router.get("", response_model=list[DocumentListItem])
async def list_documents(current_user: dict = Depends(get_current_user)):
    session = get_session()
    try:
        can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
        query = session.query(Document)
        if not can_read_all:
            query = query.filter(Document.owner_id == current_user["id"])
        docs = query.order_by(Document.created_at.desc()).limit(50).all()
        return [
            DocumentListItem(
                document_id=d.document_id,
                filename=d.filename,
                status=d.status,
                kb_id=d.kb_id,
                chunk_count=d.chunk_count,
                created_at=d.created_at.isoformat() if d.created_at else "",
            )
            for d in docs
        ]
    finally:
        session.close()


@router.get("/{document_id}", response_model=DocumentStatusResponse)
async def get_document(document_id: str, current_user: dict = Depends(get_current_user)):
    session = get_session()
    try:
        record = session.query(Document).filter_by(document_id=document_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="Document not found")
        if not current_user["is_admin"] and record.owner_id != current_user["id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        return DocumentStatusResponse(
            document_id=record.document_id,
            filename=record.filename,
            status=record.status,
            chunk_count=record.chunk_count,
        )
    finally:
        session.close()
