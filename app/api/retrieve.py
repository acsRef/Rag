"""只检索不生成端点：数据字典桥与外部消费方的检索入口。

与 /chat/stream 的区别：不走意图识别/改写/cross-doc/重排/MMR，
kb_ids 由调用方 pin 死，hybrid_search 直调——防止跨知识库污染。
鉴权：admin / doc.read_all bypass；否则按 KB visibility + 角色访问判定
（与 list_kb 语义一致）。
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.core.retrieval import embed_query_with_fallback
from app.middleware.auth import get_current_user
from app.models.schemas import RetrieveRequest, RetrieveResponse, RetrievedItem
from app.store.db import KBRoleAccess, KnowledgeBase, get_session
from app.store.pgvector_store import hybrid_search

router = APIRouter(prefix="/api/v1/retrieve", tags=["retrieve"])


def _assert_kb_readable(session, user: dict, kb_ids: list[str]) -> None:
    """逐 KB 判定可读性；任一无权 → 403 并指明 kb_id。

    语义与 list_kb 一致：admin / doc.read_all 全量可读；
    否则 public 可读、owner 可读、internal/restricted 需 KBRoleAccess 角色命中。
    不存在的 kb_id 同样 403（不向无权者泄露存在性）。
    """
    if user["is_admin"] or "doc.read_all" in user["permissions"]:
        return
    for kb_id in kb_ids:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            raise HTTPException(status_code=403, detail=f"知识库不存在或不可读: {kb_id}")
        if kb.visibility == "public" or kb.owner_id == user["id"]:
            continue
        role_ids = user.get("role_ids") or []
        hit = None
        if role_ids and kb.visibility in ("internal", "restricted"):
            hit = (
                session.query(KBRoleAccess)
                .filter(KBRoleAccess.kb_id == kb_id, KBRoleAccess.role_id.in_(role_ids))
                .first()
            )
        if not hit:
            raise HTTPException(status_code=403, detail=f"无权读取知识库: {kb_id}")


@router.post("", response_model=RetrieveResponse)
async def retrieve(body: RetrieveRequest, current_user: dict = Depends(get_current_user)):
    # 同步 DB 调用必须 to_thread，否则阻塞事件循环（与 retrieval/documents 同律）
    session = get_session()
    try:
        await asyncio.to_thread(_assert_kb_readable, session, current_user, body.kb_ids)
    finally:
        session.close()

    query_emb, degraded = await embed_query_with_fallback(body.query)
    if query_emb is None:
        # 纯向量模式 + embedding 失败：零向量余弦排序未定义，宁可空结果
        return RetrieveResponse(items=[], degraded=True)

    rows = await asyncio.to_thread(
        hybrid_search,
        kb_ids=body.kb_ids,
        embedding=query_emb,
        query=body.query,
        user_role_ids=current_user.get("role_ids"),
        can_read_all=current_user["is_admin"] or "doc.read_all" in current_user["permissions"],
        top_k=body.top_k,
        enable_question_channel=settings.question_channel_enabled,
        user_id=current_user["id"],
    )
    items = [
        RetrievedItem(
            chunk_id=r.get("chunk_id", ""),
            document_id=r.get("document_id", ""),
            text=r.get("text", ""),
            title=r.get("title", ""),
            section_path=r.get("section_path", ""),
            score=float(r.get("score", 0.0)),
        )
        for r in rows
    ]
    return RetrieveResponse(items=items, degraded=degraded)
