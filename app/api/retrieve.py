"""只检索不生成端点：数据字典桥与外部消费方的检索入口。

与 /chat/stream 的区别：不走意图识别/改写/cross-doc/重排/MMR，
kb_ids 由调用方 pin 死，检索函数直调——防止跨知识库污染。
熔断决策：本端点面向机器消费方，PG 故障 fail-loud（500）为有意决策——
不接入 chat 管线的 postgres 熔断闸门（retrieval._search_kb 的
provider_health allow_request/on_failure）与降级计数，由调用方自行感知超时/重试。
鉴权：admin / doc.read_all bypass；否则按 KB visibility + 角色访问判定
（与 list_kb 语义一致）。
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.core.retrieval import embed_query_with_fallback
from app.middleware.auth import get_current_user
from app.models.schemas import RetrievedItem, RetrieveRequest, RetrieveResponse
from app.store.db import KBRoleAccess, KnowledgeBase, get_session
from app.store.pgvector_store import hybrid_search, search

logger = logging.getLogger(__name__)

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
            logger.warning("retrieve.kb_denied user_id=%s kb_id=%s reason=missing_or_unreadable",
                           user["id"], kb_id)
            raise HTTPException(status_code=403, detail=f"知识库不存在或不可读: {kb_id}")
        if kb.visibility == "public" or kb.owner_id == user["id"]:
            continue
        role_ids = user["role_ids"]  # _build_user_dict 保证键齐全，直接索引（与 list_kb 一致）
        hit = None
        if role_ids and kb.visibility in ("internal", "restricted"):
            hit = (
                session.query(KBRoleAccess)
                .filter(KBRoleAccess.kb_id == kb_id, KBRoleAccess.role_id.in_(role_ids))
                .first()
            )
        if not hit:
            logger.warning("retrieve.kb_denied user_id=%s kb_id=%s reason=no_role_access",
                           user["id"], kb_id)
            raise HTTPException(status_code=403, detail=f"无权读取知识库: {kb_id}")


@router.post("", response_model=RetrieveResponse)
async def retrieve(body: RetrieveRequest, current_user: dict = Depends(get_current_user)):
    # 鉴权前去重保序：重复 kb_id 不应放大检索面，也不改变授权判定顺序
    kb_ids = list(dict.fromkeys(body.kb_ids))
    # 同步 DB 调用必须 to_thread，否则阻塞事件循环（与 retrieval/documents 同律）
    session = get_session()
    try:
        await asyncio.to_thread(_assert_kb_readable, session, current_user, kb_ids)
    finally:
        session.close()

    query_emb, degraded = await embed_query_with_fallback(body.query)
    if query_emb is None:
        # 纯向量模式 + embedding 熔断/失败：零向量余弦排序未定义，宁可空结果
        logger.warning("retrieve.pure_vector_degraded — embedding unavailable with hybrid off, "
                       "returning empty items")
        return RetrieveResponse(items=[], degraded=True)

    user_role_ids = current_user["role_ids"]  # _build_user_dict 保证键齐全（与 list_kb 一致）
    can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
    # 镜像主路径 retrieval._search_kb 的分支：hybrid on → hybrid_search（settings 参数显式透传），
    # hybrid off → 纯向量 search（签名无 query/BM25/question 通道参数，行级权限参数等价）
    if settings.hybrid_search_enabled:
        rows = await asyncio.to_thread(
            hybrid_search,
            kb_ids=kb_ids,
            embedding=query_emb,
            query=body.query,
            user_role_ids=user_role_ids,
            can_read_all=can_read_all,
            top_k=body.top_k,
            fetch_k=settings.hybrid_search_top_k,
            rrf_k=settings.hybrid_rrf_k,
            enable_question_channel=settings.question_channel_enabled,
            user_id=current_user["id"],
        )
    else:
        rows = await asyncio.to_thread(
            search,
            kb_ids=kb_ids,
            embedding=query_emb,
            user_role_ids=user_role_ids,
            can_read_all=can_read_all,
            top_k=body.top_k,
            user_id=current_user["id"],
        )
    # 行键集由检索层契约固定（chunk_id/document_id/text/title/section_path/score）：
    # 契约漂移必须响亮失败（KeyError → 500），不用 .get 兜底返回空串脏数据
    items = [
        RetrievedItem(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            text=r["text"],
            title=r["title"],
            section_path=r["section_path"],
            score=float(r["score"]),
        )
        for r in rows
    ]
    return RetrieveResponse(items=items, degraded=degraded)
