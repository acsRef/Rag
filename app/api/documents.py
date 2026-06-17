from app.ingestion.pipeline import ingestion_pipeline
from app.store.db import get_session, DocumentRecord
from app.models.schemas import DocumentUploadResponse, DocumentStatusResponse
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Optional

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(default="default"),
):
    content = await file.read()
    result = ingestion_pipeline.run(
        filename=file.filename or "unknown",
        content=content,
        kb_id=kb_id,
    )
    return DocumentUploadResponse(**result)


@router.get("/{document_id}", response_model=DocumentStatusResponse)
async def get_document(document_id: str):
    session = get_session()
    try:
        record = session.query(DocumentRecord).filter_by(document_id=document_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="Document not found")
        return DocumentStatusResponse(
            document_id=record.document_id,
            filename=record.filename,
            status=record.status,
            chunk_count=record.chunk_count,
        )
    finally:
        session.close()
