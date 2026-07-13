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
import time
import jieba
from sqlalchemy import text
from app.store.db import get_session, Chunk, utc_now
from app.config import settings

logger = logging.getLogger(__name__)


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
                created_at=base_ts.replace(microsecond=base_ts.microsecond + i),
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
) -> list[dict]:
    """Vector cosine similarity search with role-based access control.

    If can_read_all is True (admin with doc.read_all permission), no ACL filter.
    Otherwise filters to chunks where:
      - visibility = 'public', OR
      - allowed_roles overlaps with user_role_ids (PostgreSQL && operator)
    """
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
                       AND allowed_roles && :user_roles))
            ORDER BY embedding <=> (:query)::vector
            LIMIT :top_k
        """
        rows = session.execute(text(sql), {
            "query": embedding,
            "kb_ids": kb_ids,
            "can_read_all": can_read_all,
            "user_roles": user_role_ids or [],
            "top_k": top_k,
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


def tokenize(text: str) -> str:
    """jieba tokenize for BM25 full-text search."""
    return " ".join(jieba.cut(text))


def bm25_search(
    kb_ids: list[str],
    query: str,
    user_role_ids: list[int] | None = None,
    can_read_all: bool = False,
    top_k: int = 10,
) -> list[dict]:
    """BM25-style lexical search using PostgreSQL ts_rank + jieba tokenization."""
    query_tokens = tokenize(query)
    session = get_session()
    t0 = time.monotonic()
    logger.debug("bm25.search.start kb_count=%d top_k=%d", len(kb_ids), top_k)
    try:
        sql = """
            SELECT chunk_id, document_id, text, embedding, title, summary,
                   section_path,
                   ts_rank(to_tsvector('simple', search_text),
                           plainto_tsquery('simple', :query)) AS score
            FROM chunks
            WHERE kb_id = ANY(:kb_ids)
              AND (:can_read_all = TRUE
                   OR visibility = 'public'
                   OR (visibility IN ('internal', 'restricted')
                       AND allowed_roles && :user_roles))
              AND to_tsvector('simple', search_text) @@ plainto_tsquery('simple', :query)
            ORDER BY score DESC
            LIMIT :top_k
        """
        rows = session.execute(text(sql), {
            "query": query_tokens,
            "kb_ids": kb_ids,
            "can_read_all": can_read_all,
            "user_roles": user_role_ids or [],
            "top_k": top_k,
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
) -> list[dict]:
    """Retrieve chunks by question-vector similarity (cosine).

    Multiple questions per chunk → take the MIN distance (nearest question wins).
    ACL filtering mirrors vector_search.
    """
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
                       AND c.allowed_roles && :user_roles))
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
) -> list[dict]:
    """Hybrid vector + BM25 + optional question-vector search with RRF merge.

    RRF formula: score = Σ weight / (k + rank + 1)
    k defaults to 60 (smooth long-tail ranks).
    """
    t0 = time.monotonic()
    vector_results = search(
        kb_ids, embedding, user_role_ids, can_read_all, top_k=fetch_k,
    )
    bm25_results = bm25_search(
        kb_ids, query, user_role_ids, can_read_all, top_k=fetch_k,
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
    """Delete old chunks + their question embeddings and insert new ones."""
    session = get_session()
    try:
        old_ids = [
            r[0] for r in session.query(Chunk.chunk_id)
            .filter(Chunk.document_id == document_id).all()
        ]
        if old_ids:
            session.query(ChunkQuestion).filter(
                ChunkQuestion.chunk_id.in_(old_ids)
            ).delete(synchronize_session=False)
        session.query(Chunk).filter(Chunk.document_id == document_id).delete()
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
                created_at=base_ts.replace(microsecond=base_ts.microsecond + i),
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
    chunk_ids: list[str],
    expand_n: int = 2,
) -> dict[str, dict[str, str]]:
    """Fetch neighboring chunks for a list of anchor chunk_ids.

    Uses a single SQL range query per document, avoiding full-document load.
    Returns dict keyed by anchor chunk_id.
    """
    import re

    if not chunk_ids:
        return {}

    anchors: list[tuple[str, int, str]] = []
    for cid in chunk_ids:
        m = re.match(r"^(.+)_(\d+)$", cid)
        if m:
            anchors.append((m.group(1), int(m.group(2)), cid))

    if not anchors:
        return {}

    from collections import defaultdict
    doc_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for doc_id, seq, cid in anchors:
        doc_ranges[doc_id].append((seq, cid))

    session = get_session()
    try:
        result: dict[str, dict[str, str]] = {}
        for doc_id, seqs in doc_ranges.items():
            min_seq = min(s for s, _ in seqs)
            max_seq = max(s for s, _ in seqs)
            query_min = max(0, min_seq - expand_n)
            query_max = max_seq + expand_n

            from sqlalchemy import text, bindparam
            seq_filter = text(
                "CAST(SUBSTRING(chunk_id FROM '_(\\d+)$') AS INTEGER) BETWEEN :qmin AND :qmax"
            ).bindparams(qmin=query_min, qmax=query_max)
            rows = (
                session.query(Chunk.chunk_id, Chunk.text)
                .filter(Chunk.document_id == doc_id, seq_filter)
                .order_by(Chunk.id)
                .all()
            )

            seq_map: dict[int, str] = {}
            for row in rows:
                m2 = re.match(r"^.+_(\d+)$", row.chunk_id)
                if m2:
                    seq = int(m2.group(1))
                    if query_min <= seq <= query_max:
                        seq_map[seq] = row.text

            for seq, cid in seqs:
                before_parts = []
                for i in range(seq - expand_n, seq):
                    if i >= 0 and i in seq_map:
                        before_parts.append(seq_map[i])
                after_parts = []
                for i in range(seq + 1, seq + expand_n + 1):
                    if i in seq_map:
                        after_parts.append(seq_map[i])
                result[cid] = {
                    "before": "\n".join(before_parts),
                    "after": "\n".join(after_parts),
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


def get_chunks_by_documents_bulk(
    doc_ids: list[str],
    user_role_ids: list[int] | None = None,
    can_read_all: bool = False,
) -> dict[str, list[dict]]:
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
                       AND allowed_roles && :user_roles))
        """
        rows = session.execute(text(sql), {
            "doc_ids": doc_ids,
            "can_read_all": can_read_all,
            "user_roles": user_role_ids or [],
        }).fetchall()
        result: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
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
