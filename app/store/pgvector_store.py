"""pgvector-based vector search + BM25 lexical search with permission filtering.

检索方法:
  - `search`: 纯向量余弦相似度检索
  - `bm25_search`: PostgreSQL ts_rank + jieba 分词的全文检索
  - `hybrid_search`: 上述两者用 RRF 倒数排名融合

权限过滤:每个 chunk 在 SQL 层带 `visibility` / `allowed_roles`,按用户角色过滤。
  过滤逻辑对所有检索方法一致:admin (can_read_all=True) 跳过,否则只返回
  visibility='public' 或 allowed_roles 与用户角色有交集的 chunk。
"""
import logging
import re
import time
from datetime import timedelta

import jieba
from sqlalchemy import text, func
from app.store.db import get_session, Chunk, ChunkQuestion, utc_now
from app.config import settings

logger = logging.getLogger(__name__)

# ── 通用中文停用词表 (用于 BM25 索引和查询端去噪) ─────────────
# 设计审查 P2-13：单一共享定义，见 app/core/stopwords.py
from app.core.stopwords import STOP_WORDS as _STOP_WORDS


def add_chunks(chunks_data: list[dict]):
    """Bulk insert chunks with embeddings.

    chunks_data: [{chunk_id, document_id, kb_id, text, embedding, title,
                   summary, questions, section_path, search_text,
                   content_hash, visibility, allowed_roles}]
    """
    session = get_session()
    try:
        base_ts = utc_now()
        for i, c in enumerate(chunks_data):
            session.add(Chunk(
                chunk_id=c["chunk_id"],
                document_id=c["document_id"],
                kb_id=c["kb_id"],
                text=c["text"],
                embedding=c["embedding"],
                title=c.get("title", ""),
                summary=c.get("summary", ""),
                questions=c.get("questions", ""),
                section_path=c.get("section_path", ""),
                search_text=c.get("search_text", ""),
                content_hash=c.get("content_hash", ""),
                visibility=c.get("visibility", "public"),
                allowed_roles=c.get("allowed_roles", []),
                created_at=base_ts + timedelta(microseconds=i),  # timedelta 自动进位，不再微秒溢出
            ))
        session.commit()
    finally:
        session.close()


def get_chunks_by_document(document_id: str) -> list[dict]:
    """Return all chunks for a document, keyed by content_hash for reuse lookup."""
    session = get_session()
    try:
        rows = (
            session.query(Chunk)
            .filter(Chunk.document_id == document_id)
            .all()
        )
        return [
            {
                "chunk_id": r.chunk_id,
                "document_id": r.document_id,
                "text": r.text,
                "embedding": r.embedding,
                "title": r.title,
                "summary": r.summary,
                "questions": r.questions,
                "section_path": r.section_path,
                "search_text": r.search_text,
                "content_hash": r.content_hash,
                "visibility": r.visibility,
                "allowed_roles": r.allowed_roles,
            }
            for r in rows
        ]
    finally:
        session.close()


def search(
    kb_ids: list[str],
    embedding: list[float],
    user_role_ids: list[int] | None = None,
    can_read_all: bool = False,
    top_k: int = 10,
    user_id: str = "",
) -> list[dict]:
    """Vector cosine similarity search with role-based access control.

    If can_read_all is True (admin with doc.read_all permission), no ACL filter.
    Otherwise filters to chunks where:
      - visibility = 'public', OR
      - allowed_roles overlaps with user_role_ids (PostgreSQL && operator), OR
      - 属主旁路：chunk 所属文档的 owner 是当前用户（个人工作空间
        restricted 文档对属主本人必须可检索，对他人不可见）
    """
    rows = []  # finally 的 debug 日志引用 rows；execute 抛错时不得再抛 UnboundLocalError 掩盖原始异常
    session = get_session()
    t0 = time.monotonic()
    logger.debug("vector.search.start kb_count=%d top_k=%d can_read_all=%s", len(kb_ids), top_k, can_read_all)
    try:
        sql = """
            SELECT chunk_id, document_id, text, embedding, title, summary,
                   section_path, 1 - (embedding <=> (:query)::vector) AS score
            FROM chunks
            WHERE kb_id = ANY(:kb_ids)
              AND (:can_read_all = TRUE
                   OR visibility = 'public'
                   OR (visibility IN ('internal', 'restricted')
                       AND allowed_roles && :user_roles)
                   OR (:user_id <> '' AND EXISTS (
                         SELECT 1 FROM documents d
                         WHERE d.document_id = chunks.document_id
                           AND d.owner_id = :user_id)))
            ORDER BY embedding <=> (:query)::vector
            LIMIT :top_k
        """
        rows = session.execute(text(sql), {
            "query": embedding,
            "kb_ids": kb_ids,
            "can_read_all": can_read_all,
            "user_roles": user_role_ids or [],
            "top_k": top_k,
            "user_id": user_id or "",
        }).fetchall()

        return [
            {
                "chunk_id": r[0],
                "document_id": r[1],
                "text": r[2],
                "embedding": r[3],
                "title": r[4],
                "summary": r[5],
                "section_path": r[6],
                "score": float(r[7]),
            }
            for r in rows
        ]
    finally:
        session.close()
        logger.debug("vector.search.done row_count=%d elapsed_ms=%.1f", len(rows), (time.monotonic() - t0) * 1000)


# tsquery 运算符/分隔符字符——jieba 分词结果若携带这些字符（如查询 "C++"、
# 带引号/括号的短语），拼进 to_tsquery 会直接 SQL 语法错误，异常被
# _search_kb 吞掉后 BM25 通道静默消失。只保留词字符与连字符。
_TSQUERY_UNSAFE_RE = re.compile(r"[^\w-]", re.UNICODE)


def _sanitize_ts_token(tok: str) -> str:
    cleaned = _TSQUERY_UNSAFE_RE.sub("", tok)
    # 纯符号 token（+++ 之类）清洗后为空或只剩连字符：丢弃，
    # 裸 '-' 进 tsquery 同样是语法隐患
    if not cleaned or not re.search(r"\w", cleaned, re.UNICODE):
        return ""
    return cleaned


def tokenize(text: str, stopwords: bool = False) -> str:
    """jieba tokenize for BM25 full-text search.

    Args:
        text: Input text (Chinese or mixed).
        stopwords: If True, remove _STOP_WORDS tokens from the result.

    Returns:
        Space-separated tokens ready for PostgreSQL tsvector/tsquery.
        Tokens are sanitized so they can never break to_tsquery syntax.
    """
    tokens = jieba.cut(text)
    if stopwords:
        tokens = [t for t in tokens if t not in _STOP_WORDS]
    cleaned = [_sanitize_ts_token(t) for t in tokens]
    return " ".join(t for t in cleaned if t)


def bm25_search(
    kb_ids: list[str],
    query: str,
    user_role_ids: list[int] | None = None,
    can_read_all: bool = False,
    top_k: int = 10,
    stopwords: bool = True,
    user_id: str = "",
) -> list[dict]:
    """BM25-style lexical search using PostgreSQL ts_rank + jieba tokenization.

    Uses OR-based tsquery so that a chunk matching any query term is found
    (not all). plainto_tsquery uses AND which is too strict — user queries
    often contain words like "表示" or "什么" that don't appear in documents.

    Args:
        stopwords: If True, remove common stop words from the query
                   (e.g. "什么" "表示" "怎么") to reduce noise in BM25 matching.
                   Set to False for a relaxed fallback pass.
    """
    query_tokens = tokenize(query, stopwords=stopwords)
    # Convert AND-based plainto_tsquery to OR-based to_tsquery
    # "绿色 闪烁 表示 什么" → "绿色 | 闪烁 | 表示 | 什么"
    or_query = " | ".join(query_tokens.split()) if query_tokens.strip() else query_tokens
    if not or_query.strip():
        return []
    rows = []  # 同 search()：防 finally 日志以 UnboundLocalError 掩盖原始异常
    session = get_session()
    t0 = time.monotonic()
    logger.debug("bm25.search.start kb_count=%d top_k=%d", len(kb_ids), top_k)
    try:
        sql = """
            SELECT chunk_id, document_id, text, embedding, title, summary,
                   section_path,
                   ts_rank(to_tsvector('simple', search_text),
                           to_tsquery('simple', :or_query)) AS score
            FROM chunks
            WHERE kb_id = ANY(:kb_ids)
              AND (:can_read_all = TRUE
                   OR visibility = 'public'
                   OR (visibility IN ('internal', 'restricted')
                       AND allowed_roles && :user_roles)
                   OR (:user_id <> '' AND EXISTS (
                         SELECT 1 FROM documents d
                         WHERE d.document_id = chunks.document_id
                           AND d.owner_id = :user_id)))
              AND to_tsvector('simple', search_text) @@ to_tsquery('simple', :or_query)
            ORDER BY score DESC
            LIMIT :top_k
        """
        rows = session.execute(text(sql), {
            "or_query": or_query,
            "kb_ids": kb_ids,
            "can_read_all": can_read_all,
            "user_roles": user_role_ids or [],
            "top_k": top_k,
            "user_id": user_id or "",
        }).fetchall()

        return [
            {
                "chunk_id": r[0],
                "document_id": r[1],
                "text": r[2],
                "embedding": r[3],
                "title": r[4],
                "summary": r[5],
                "section_path": r[6],
                "score": float(r[7]),
            }
            for r in rows
        ]
    finally:
        session.close()
        logger.debug("bm25.search.done row_count=%d elapsed_ms=%.1f", len(rows), (time.monotonic() - t0) * 1000)


# ── Chunk Questions (multi-channel retrieval) ─────────────


def upsert_chunk_questions(questions_data: list[dict]):
    """Insert or update chunk question embeddings.

    questions_data: [{chunk_id, question, embedding, position}]
    Deletes existing questions for affected chunk_ids first, then inserts.
    """
    if not questions_data:
        return
    session = get_session()
    try:
        chunk_ids = list(set(q["chunk_id"] for q in questions_data))
        session.execute(
            text("DELETE FROM chunk_questions WHERE chunk_id = ANY(:cids)"),
            {"cids": chunk_ids},
        )
        for q in questions_data:
            session.execute(
                text("INSERT INTO chunk_questions (chunk_id, question, embedding, position) "
                     "VALUES (:chunk_id, :question, :embedding, :position)"),
                {
                    "chunk_id": q["chunk_id"],
                    "question": q["question"],
                    "embedding": q["embedding"],
                    "position": q.get("position", 0),
                },
            )
        session.commit()
    finally:
        session.close()


def question_vector_search(
    kb_ids: list[str],
    query_emb: list[float],
    user_role_ids: list[int] | None = None,
    can_read_all: bool = False,
    top_k: int = 20,
    user_id: str = "",
) -> list[dict]:
    """Retrieve chunks by question-vector similarity (cosine).

    Multiple questions per chunk → take the MIN distance (nearest question wins).
    ACL filtering mirrors vector_search.
    """
    rows = []  # 同 search()：防 finally 日志以 UnboundLocalError 掩盖原始异常
    session = get_session()
    t0 = time.monotonic()
    logger.debug("question_vector.search.start kb_count=%d top_k=%d", len(kb_ids), top_k)
    try:
        sql = """
            SELECT c.chunk_id, c.document_id, c.text, c.embedding, c.title, c.summary,
                   c.section_path,
                   1 - MIN(q.embedding <=> (:query)::vector) AS score
            FROM chunk_questions q
            JOIN chunks c ON c.chunk_id = q.chunk_id
            WHERE c.kb_id = ANY(:kb_ids)
              AND (:can_read_all = TRUE
                   OR c.visibility = 'public'
                   OR (c.visibility IN ('internal', 'restricted')
                       AND c.allowed_roles && :user_roles)
                   OR (:user_id <> '' AND EXISTS (
                         SELECT 1 FROM documents d
                         WHERE d.document_id = c.document_id
                           AND d.owner_id = :user_id)))
            GROUP BY c.chunk_id, c.document_id, c.text, c.embedding, c.title,
                     c.summary, c.section_path
            ORDER BY MIN(q.embedding <=> (:query)::vector)
            LIMIT :top_k
        """
        rows = session.execute(text(sql), {
            "query": query_emb,
            "kb_ids": kb_ids,
            "can_read_all": can_read_all,
            "user_roles": user_role_ids or [],
            "top_k": top_k,
            "user_id": user_id or "",
        }).fetchall()

        return [
            {
                "chunk_id": r[0],
                "document_id": r[1],
                "text": r[2],
                "embedding": r[3],
                "title": r[4],
                "summary": r[5],
                "section_path": r[6],
                "score": float(r[7]),
            }
            for r in rows
        ]
    finally:
        session.close()
        logger.debug("question_vector.search.done row_count=%d elapsed_ms=%.1f",
                     len(rows), (time.monotonic() - t0) * 1000)


def hybrid_search(
    kb_ids: list[str],
    embedding: list[float],
    query: str,
    user_role_ids: list[int] | None = None,
    can_read_all: bool = False,
    top_k: int = 10,
    fetch_k: int = 20,
    rrf_k: int = 60,
    enable_question_channel: bool = False,
    user_id: str = "",
) -> list[dict]:
    """Hybrid vector + BM25 + optional question-vector search with RRF merge.

    RRF formula: score = Σ weight / (k + rank + 1)
    k defaults to 60 (smooth long-tail ranks).
    """
    t0 = time.monotonic()
    vector_results = search(
        kb_ids, embedding, user_role_ids, can_read_all, top_k=fetch_k,
        user_id=user_id,
    )
    bm25_results = bm25_search(
        kb_ids, query, user_role_ids, can_read_all, top_k=fetch_k,
        user_id=user_id,
    )

    channel_weights: dict[str, float] = {}
    rrf_scores: dict[str, float] = {}

    def _accumulate(results: list[dict], channel: str, weight: float = 1.0):
        channel_weights[channel] = weight
        for rank, r in enumerate(results):
            rrf_scores[r["chunk_id"]] = rrf_scores.get(r["chunk_id"], 0) + weight / (rrf_k + rank + 1)

    _accumulate(vector_results, "vector")
    _accumulate(bm25_results, "bm25")

    question_results = []
    if enable_question_channel:
        question_results = question_vector_search(
            kb_ids, embedding, user_role_ids, can_read_all,
            top_k=settings.question_channel_top_k,
            user_id=user_id,
        )
        _accumulate(question_results, "question",
                    weight=settings.question_channel_rrf_weight)

    merged: dict[str, dict] = {}
    for r in vector_results:
        merged[r["chunk_id"]] = r
    for r in bm25_results:
        merged[r["chunk_id"]] = r
    for r in question_results:
        merged[r["chunk_id"]] = r

    ranked = sorted(merged.values(), key=lambda r: rrf_scores[r["chunk_id"]], reverse=True)

    # ── Fallback: 如果 RRF 融合结果不足 top_k，用原始 query 重试 BM25 ──
    if len(ranked) < top_k:
        relaxed_bm25 = bm25_search(
            kb_ids, query, user_role_ids, can_read_all,
            top_k=fetch_k, stopwords=False, user_id=user_id,
        )
        existing_ids = {r["chunk_id"] for r in ranked}
        new_from_bm25 = [r for r in relaxed_bm25 if r["chunk_id"] not in existing_ids]
        if new_from_bm25:
            logger.info(
                "hybrid.fallback relaxed_bm25 new=%d had=%d target=%d",
                len(new_from_bm25), len(ranked), top_k,
            )
            for rank, r in enumerate(new_from_bm25):
                rrf_scores[r["chunk_id"]] = rrf_scores.get(r["chunk_id"], 0) + 0.5 / (rrf_k + rank + 1)
                ranked.append(r)
            ranked.sort(key=lambda r: rrf_scores[r["chunk_id"]], reverse=True)

    for r in ranked:
        r["score"] = rrf_scores[r["chunk_id"]]

    logger.info(
        "hybrid.search.done vec=%d bm25=%d qvec=%d merged=%d rrf_k=%d "
        "channels=%s elapsed_ms=%.1f",
        len(vector_results), len(bm25_results), len(question_results),
        len(merged), rrf_k, list(channel_weights.keys()),
        (time.monotonic() - t0) * 1000,
    )
    return ranked[:top_k]


def replace_chunks(document_id: str, chunks_data: list[dict]):
    """差量 upsert：只删消失的 chunk，复用行 UPDATE 保留，新行 INSERT。

    旧实现全删全插：chunk_questions 外键 ON DELETE CASCADE 会把复用 chunk 的
    问题向量一并删光，而索引侧重建只覆盖新 chunk → 每次增量更新都在静默损耗
    question 通道。差量化后复用 chunk 的行不删，其问题行随外键存活。

    created_at 统一刷新为 base_ts + i 微秒：始终编码当前逻辑顺序——
    get_neighbor_chunks 依此定位邻居（Chunk.id 不行：后插入的 chunk id 更大，
    逻辑位置却可能在中间）。
    """
    session = get_session()
    try:
        existing_ids = {
            r[0] for r in session.query(Chunk.chunk_id)
            .filter(Chunk.document_id == document_id).all()
        }
        new_ids = {c["chunk_id"] for c in chunks_data}
        gone_ids = existing_ids - new_ids
        if gone_ids:
            session.query(Chunk).filter(
                Chunk.document_id == document_id,
                Chunk.chunk_id.in_(list(gone_ids)),
            ).delete(synchronize_session=False)

        base_ts = utc_now()
        for i, c in enumerate(chunks_data):
            ts = base_ts + timedelta(microseconds=i)  # timedelta 自动进位，不再微秒溢出
            values = dict(
                kb_id=c["kb_id"],
                text=c["text"],
                embedding=c["embedding"],
                title=c.get("title", ""),
                summary=c.get("summary", ""),
                questions=c.get("questions", ""),
                section_path=c.get("section_path", ""),
                search_text=c.get("search_text", ""),
                content_hash=c.get("content_hash", ""),
                visibility=c.get("visibility", "public"),
                allowed_roles=c.get("allowed_roles", []),
                created_at=ts,
            )
            if c["chunk_id"] in existing_ids:
                session.query(Chunk).filter(Chunk.chunk_id == c["chunk_id"]).update(
                    values, synchronize_session=False)
            else:
                session.add(Chunk(
                    chunk_id=c["chunk_id"],
                    document_id=c["document_id"],
                    **values,
                ))
        session.commit()
    finally:
        session.close()


def list_kb_ids() -> list[str]:
    """Return all distinct kb_ids that have chunks."""
    session = get_session()
    try:
        rows = session.query(Chunk.kb_id).distinct().all()
        return [r[0] for r in rows]
    finally:
        session.close()


def get_neighbor_chunks(
    anchors: list[tuple[str, str]],
    expand_n: int = 2,
) -> dict[str, dict[str, str]]:
    """Fetch neighboring chunks for anchor (document_id, chunk_id) pairs.

    稳定 hash chunk id 不再携带序号（旧实现按 `_(\\d+)$` 解析尾部序号，
    id 改造后静默失效）。改按 created_at 全序定位：replace_chunks 差量
    upsert 每次增量写入都整体刷新 created_at，始终编码当前逻辑顺序
    （Chunk.id 不可用——后插入的 chunk id 更大，逻辑位置却可能在中间）。

    Returns dict keyed by anchor chunk_id: {"before": str, "after": str}.
    """
    if not anchors:
        return {}

    from collections import defaultdict
    doc_anchors: dict[str, list[str]] = defaultdict(list)
    for doc_id, cid in anchors:
        if doc_id and cid:
            doc_anchors[doc_id].append(cid)
    if not doc_anchors:
        return {}

    session = get_session()
    try:
        result: dict[str, dict[str, str]] = {}
        for doc_id, cids in doc_anchors.items():
            rows = (
                session.query(Chunk.chunk_id, Chunk.text)
                .filter(Chunk.document_id == doc_id)
                .order_by(Chunk.created_at, Chunk.id)
                .all()
            )
            order = [r.chunk_id for r in rows]
            text_map = {r.chunk_id: r.text for r in rows}
            pos = {cid: i for i, cid in enumerate(order)}
            for cid in cids:
                idx = pos.get(cid)
                if idx is None:
                    continue
                before = [text_map[order[j]] for j in range(max(0, idx - expand_n), idx)]
                after = [text_map[order[j]] for j in range(idx + 1, min(len(order), idx + 1 + expand_n))]
                result[cid] = {
                    "before": "\n".join(before),
                    "after": "\n".join(after),
                }
        return result
    finally:
        session.close()


# ── Cross-Doc Relation Store Methods ────────────────────

def save_doc_entities(document_id: str, entities: list[tuple[str, int]]):
    from app.store.db import DocEntity
    session = get_session()
    try:
        session.query(DocEntity).filter(DocEntity.document_id == document_id).delete()
        for entity, freq in entities:
            session.add(DocEntity(document_id=document_id, entity=entity, frequency=freq))
        session.commit()
    finally:
        session.close()


def get_doc_entities_bulk(doc_ids: list[str]) -> dict[str, list[tuple[str, int]]]:
    if not doc_ids:
        return {}
    from app.store.db import DocEntity
    session = get_session()
    try:
        rows = (
            session.query(DocEntity)
            .filter(DocEntity.document_id.in_(doc_ids))
            .order_by(DocEntity.document_id, DocEntity.frequency.desc())
            .all()
        )
        result: dict[str, list[tuple[str, int]]] = {}
        for r in rows:
            if r.document_id not in result:
                result[r.document_id] = []
            result[r.document_id].append((r.entity, r.frequency))
        return result
    finally:
        session.close()


def get_all_doc_ids_with_entities(kb_ids: list[str] | None = None) -> list[str]:
    from app.store.db import DocEntity, Document
    session = get_session()
    try:
        q = session.query(DocEntity.document_id).distinct()
        if kb_ids:
            q = q.join(Document, DocEntity.document_id == Document.document_id).filter(
                Document.kb_id.in_(kb_ids)
            )
        rows = q.all()
        return [r[0] for r in rows]
    finally:
        session.close()


def get_global_df() -> tuple[dict[str, int], int]:
    """设计审查 P1-8：SQL 聚合全局 DF，避免把全量实体拖进内存。

    Returns (df_dict, total_docs)：df_dict[entity] = 含该实体的不同文档数。
    doc_entities 只存 freq>=2 的实体（extract 已过滤），等价于旧内存版
    refresh_global_stats 的"seen_in_doc 去重后计数"。
    """
    from app.store.db import DocEntity
    session = get_session()
    try:
        rows = (
            session.query(
                DocEntity.entity,
                func.count(func.distinct(DocEntity.document_id)),
            ).group_by(DocEntity.entity).all()
        )
        df = {entity: cnt for entity, cnt in rows}
        total = session.query(
            func.count(func.distinct(DocEntity.document_id))
        ).scalar() or 0
        return df, total
    finally:
        session.close()


def get_doc_ids_with_any_entity(terms: list[str]) -> list[str]:
    """candidate 收敛：返回含任一给定实体的文档 id（去重）。

    供 DocRelationBuilder.update_for_document 只评估与本文档有实体重叠的
    文档，替代 get_all_doc_ids_with_entities + get_doc_entities_bulk(all)。
    """
    if not terms:
        return []
    from app.store.db import DocEntity
    session = get_session()
    try:
        rows = (
            session.query(DocEntity.document_id)
            .filter(DocEntity.entity.in_(terms))
            .distinct()
            .all()
        )
        return [r[0] for r in rows]
    finally:
        session.close()


def get_doc_relations(doc_id: str) -> list[dict]:
    from app.store.db import DocRelation
    session = get_session()
    try:
        rows = (
            session.query(DocRelation)
            .filter(DocRelation.source_doc == doc_id)
            .all()
        )
        return [
            {
                "target_doc": r.target_doc,
                "cosine": r.cosine,
                "cosine_scaled": r.cosine / 1000.0,
                "entity_jaccard": r.entity_jaccard,
                "relation_type": r.relation_type,
            }
            for r in rows
        ]
    finally:
        session.close()


def replace_doc_relations(source_doc: str, relations: list[dict]):
    from app.store.db import DocRelation
    session = get_session()
    try:
        session.query(DocRelation).filter(
            DocRelation.source_doc == source_doc
        ).delete()
        session.query(DocRelation).filter(
            DocRelation.target_doc == source_doc
        ).delete()
        for rel in relations:
            session.add(DocRelation(
                source_doc=rel["source_doc"],
                target_doc=rel["target_doc"],
                cosine=rel["cosine"],
                entity_jaccard=rel["entity_jaccard"],
                relation_type=rel.get("relation_type", "unknown"),
            ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def clear_all_relations():
    from app.store.db import DocRelation
    session = get_session()
    try:
        session.query(DocRelation).delete()
        session.commit()
    finally:
        session.close()


def delete_doc_relations_by_doc_id(doc_id: str):
    from app.store.db import DocRelation
    session = get_session()
    try:
        session.query(DocRelation).filter(
            DocRelation.source_doc == doc_id
        ).delete()
        session.query(DocRelation).filter(
            DocRelation.target_doc == doc_id
        ).delete()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bulk_save_relations(relations: list[dict]):
    from app.store.db import DocRelation
    session = get_session()
    try:
        for rel in relations:
            session.add(DocRelation(
                source_doc=rel["source_doc"],
                target_doc=rel["target_doc"],
                cosine=rel["cosine"],
                entity_jaccard=rel["entity_jaccard"],
                relation_type=rel.get("relation_type", "unknown"),
            ))
        session.commit()
    finally:
        session.close()


def get_doc_embedding(document_id: str) -> list[float] | None:
    from app.store.db import DocEmbedding
    session = get_session()
    try:
        row = (
            session.query(DocEmbedding.embedding)
            .filter(DocEmbedding.document_id == document_id)
            .first()
        )
        if row and row.embedding is not None:
            return list(row.embedding)
        return None
    finally:
        session.close()


def get_doc_embeddings_bulk(doc_ids: list[str]) -> dict[str, list[float]]:
    """批量取文档级 embedding：{document_id: embedding}（跳过 NULL）。"""
    if not doc_ids:
        return {}
    from app.store.db import DocEmbedding
    session = get_session()
    try:
        rows = (
            session.query(DocEmbedding.document_id, DocEmbedding.embedding)
            .filter(DocEmbedding.document_id.in_(doc_ids))
            .all()
        )
        return {r.document_id: list(r.embedding) for r in rows if r.embedding is not None}
    finally:
        session.close()


def upsert_doc_embedding(document_id: str, embedding: list[float], chunk_count: int):
    from app.store.db import DocEmbedding, utc_now
    session = get_session()
    try:
        existing = (
            session.query(DocEmbedding)
            .filter(DocEmbedding.document_id == document_id)
            .first()
        )
        if existing:
            existing.embedding = embedding
            existing.chunk_count = chunk_count
            existing.updated_at = utc_now()
        else:
            session.add(DocEmbedding(
                document_id=document_id,
                embedding=embedding,
                chunk_count=chunk_count,
            ))
        session.commit()
    finally:
        session.close()




_CHUNKS_PER_NEIGHBOR_DOC = 10   # 跨文档邻居每文档上限：代表性上下文即可，最终条数由 rerank_top_k 收口


def get_chunks_by_documents_bulk(
    doc_ids: list[str],
    user_role_ids: list[int] | None = None,
    can_read_all: bool = False,
    user_id: str = "",
) -> dict[str, list[dict]]:
    """批量取多文档 chunk。

    按 (document_id, id) 确定性排序，每文档最多保留
    _CHUNKS_PER_NEIGHBOR_DOC 条——防止大文档邻居把 rerank 淹没。
    """
    if not doc_ids:
        return {}
    session = get_session()
    try:
        from collections import defaultdict
        sql = """
            SELECT chunk_id, document_id, text, embedding, title, summary,
                   section_path, search_text, content_hash, visibility, allowed_roles
            FROM chunks
            WHERE document_id = ANY(:doc_ids)
              AND (:can_read_all = TRUE
                   OR visibility = 'public'
                   OR (visibility IN ('internal', 'restricted')
                       AND allowed_roles && :user_roles)
                   OR (:user_id <> '' AND EXISTS (
                         SELECT 1 FROM documents d2
                         WHERE d2.document_id = chunks.document_id
                           AND d2.owner_id = :user_id)))
            ORDER BY document_id, id
        """
        rows = session.execute(text(sql), {
            "doc_ids": doc_ids,
            "can_read_all": can_read_all,
            "user_roles": user_role_ids or [],
            "user_id": user_id or "",
        }).fetchall()
        result: dict[str, list[dict]] = defaultdict(list)
        per_doc_count: dict[str, int] = {}
        for r in rows:
            if per_doc_count.get(r[1], 0) >= _CHUNKS_PER_NEIGHBOR_DOC:
                continue
            per_doc_count[r[1]] = per_doc_count.get(r[1], 0) + 1
            result[r[1]].append({
                "chunk_id": r[0],
                "document_id": r[1],
                "text": r[2],
                "embedding": r[3],
                "title": r[4],
                "summary": r[5],
                "section_path": r[6],
                "search_text": r[7],
                "content_hash": r[8],
                "visibility": r[9],
                "allowed_roles": r[10],
            })
        return dict(result)
    finally:
        session.close()

def _like_escape_literal(prefix: str) -> str:
    """转义 LIKE 模式中的通配符（`_`/`%`/`\\`）——chunk_id 前缀是字面量，
    旧写法 `like(doc_id + "_%")` 中的 `_` 是单字符通配符，只是恰好被
    16 位定长 id 掩盖；id 格式一变就会误伤其他文档的行。"""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def delete_orphan_chunk_questions(document_id: str, valid_chunk_ids: list[str]) -> None:
    """删除指定文档下不再存在的 chunk 的问题行（chunk 删除/历史 id 遗留后兜底）。"""
    if not document_id:
        return
    session = get_session()
    try:
        q = session.query(ChunkQuestion).filter(
            ChunkQuestion.chunk_id.like(_like_escape_literal(document_id) + "\\_%", escape="\\"))
        if valid_chunk_ids:
            q = q.filter(~ChunkQuestion.chunk_id.in_(valid_chunk_ids))
        q.delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
