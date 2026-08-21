"""Diagnostics API — serves live pipeline telemetry for tools/diagnostics.html.

安全约束：遥测含全量用户 query 与 chunk 文本，所有端点仅 admin 可访问。
查看器（tools/diagnostics.html）从磁盘打开并携带 admin token 调用本 API。
"""

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.admin import require_admin
from app.config import settings
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/api/v1/diag", tags=["diagnostics"])
DIAG_DIR = Path(settings.diagnostics_dir)

# 诊断 id / document_id 白名单：HHMMSS-hex 与 16 位 hex 均落在此字符集内。
# diag_detail 的 {id:path} 转换器允许斜杠，不校验可拼出 ../ 读目录外文件。
_SAFE_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{0,63}$")


def _is_safe_id(value: str) -> bool:
    return bool(_SAFE_ID_RE.match(value))


@router.get("/index")
def diag_index(current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    index_path = DIAG_DIR / "index.json"
    if not index_path.exists():
        return []
    try:
        with open(index_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


@router.get("/chunks")
def diag_chunks(ids: str, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    chunk_ids = [c.strip() for c in ids.split(",") if c.strip()]
    # 与 /admin/chunks 共用查询实现（此前两处重复）
    from app.api.admin import chunk_info_rows

    return chunk_info_rows(chunk_ids)


@router.get("/detail/{diag_id:path}")
def diag_detail(diag_id: str, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    if not _is_safe_id(diag_id):
        raise HTTPException(status_code=404, detail="Diagnostic not found")
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


@router.get("/chunk-docs")
def diag_chunk_docs(current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    chunk_dir = DIAG_DIR / "chunks"
    if not chunk_dir.exists():
        return []
    docs = []
    for f in sorted(chunk_dir.iterdir()):
        if f.suffix == ".json":
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                    docs.append(
                        {
                            "document_id": data.get("document_id", f.stem),
                            "filename": data.get("filename", ""),
                            "chunk_count": len(data.get("chunks", [])),
                            "section_count": len(data.get("sections", [])),
                        }
                    )
            except (json.JSONDecodeError, OSError):
                pass
    return docs


@router.get("/chunk-doc/{document_id}")
def diag_chunk_doc(document_id: str, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    if not _is_safe_id(document_id):
        raise HTTPException(status_code=404, detail="Chunk diagnostic not found")
    path = DIAG_DIR / "chunks" / f"{document_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chunk diagnostic not found")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        raise HTTPException(status_code=500, detail="Failed to read chunk diagnostic file")
