"""Diagnostics API — serves live pipeline telemetry for tools/diagnostics.html."""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from app.config import settings
from app.store.db import get_db_ctx, Chunk, Document

router = APIRouter(prefix="/api/v1/diag", tags=["diagnostics"])
DIAG_DIR = Path(settings.diagnostics_dir)


@router.get("/index")
def diag_index():
    index_path = DIAG_DIR / "index.json"
    if not index_path.exists():
        return []
    try:
        with open(index_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


@router.get("/chunks")
def diag_chunks(ids: str):
    chunk_ids = [c.strip() for c in ids.split(",") if c.strip()]
    if not chunk_ids:
        return []
    with get_db_ctx() as session:
        rows = (
            session.query(
                Chunk.chunk_id, Chunk.document_id, Chunk.kb_id,
                Chunk.title, Chunk.section_path, Chunk.text,
                Chunk.content_hash, Chunk.visibility,
                Document.filename,
            )
            .outerjoin(Document, Chunk.document_id == Document.document_id)
            .filter(Chunk.chunk_id.in_(chunk_ids))
            .all()
        )
        return [
            {
                "chunk_id": r.chunk_id, "document_id": r.document_id, "kb_id": r.kb_id,
                "filename": r.filename or "", "title": r.title or "",
                "section_path": r.section_path or "", "text": r.text[:500] if r.text else "",
                "content_hash": r.content_hash or "", "visibility": r.visibility,
            }
            for r in rows
        ]


@router.get("/detail/{diag_id:path}")
def diag_detail(diag_id: str):
    today = sorted(
        (d for d in DIAG_DIR.iterdir() if d.is_dir()),
        reverse=True,
    )
    for day_dir in today:
        detail_path = day_dir / f"{diag_id}.json"
        if detail_path.exists():
            try:
                with open(detail_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                raise HTTPException(status_code=500, detail="Failed to read diagnostic file")
    raise HTTPException(status_code=404, detail="Diagnostic not found")
