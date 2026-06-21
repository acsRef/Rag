"""pgvector-based vector search with permission filtering."""
from sqlalchemy import text
from app.store.db import get_session, Chunk, utc_now


def add_chunks(chunks_data: list[dict]):
    """Bulk insert chunks with embeddings.

    chunks_data: [{chunk_id, document_id, kb_id, text, embedding, title,
                   summary, questions, section_path, visibility, allowed_roles}]
    """
    session = get_session()
    try:
        for c in chunks_data:
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
                visibility=c.get("visibility", "public"),
                allowed_roles=c.get("allowed_roles", []),
                created_at=utc_now(),
            ))
        session.commit()
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
    try:
        sql = """
            SELECT chunk_id, text, title, summary, section_path,
                   1 - (embedding <=> :query) AS score
            FROM chunks
            WHERE kb_id = ANY(:kb_ids)
              AND (:can_read_all = TRUE
                   OR visibility = 'public'
                   OR (visibility IN ('internal', 'restricted')
                       AND allowed_roles && :user_roles))
            ORDER BY embedding <=> :query
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
                "text": r[1],
                "title": r[2],
                "summary": r[3],
                "section_path": r[4],
                "score": float(r[5]),
            }
            for r in rows
        ]
    finally:
        session.close()


def delete_chunks_by_document(document_id: str):
    session = get_session()
    try:
        session.query(Chunk).filter(Chunk.document_id == document_id).delete()
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
