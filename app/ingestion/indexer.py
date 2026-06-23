"""Full indexing pipeline: parse → clean → structure → chunk → metadata → embed → store.

Coordinates all ingestion stages and persists results to PostgreSQL + pgvector.
"""

from app.store import pgvector_store
from app.llm.embedding import sf_embedding
from app.ingestion.chunker import text_chunker, Chunk
from app.ingestion.cleaner import document_cleaner
from app.ingestion.structurer import document_structurer
from app.ingestion.metadata import chunk_metadata_generator
from app.store.db import get_session, Document, new_id, utc_now
from app.store.pgvector_store import tokenize
from app.config import settings


class DocumentIndexer:
    def index(
        self,
        filename: str,
        content: bytes,
        kb_id: str = "default",
        user_id: str = "default_user",
        visibility: str = "public",
        allowed_roles: list[int] | None = None,
    ) -> dict:
        from app.ingestion.parser import document_parser

        text = document_parser.parse_bytes(content, filename)
        text = document_cleaner.clean(text)

        if settings.pii_enabled:
            from app.core.pii_scanner import scan_and_reject, mask_text
            rejects = scan_and_reject(text)
            if rejects:
                doc_id = new_id()
                from app.store.db import PiiAlert, PiiHold
                from app.core.pii_scanner import load_rules
                session = get_session()
                try:
                    for r in rejects:
                        session.add(PiiAlert(
                            source_type="document", source_id=doc_id,
                            rule_name=r.rule_name,
                            matched_text=r.matched_text,
                            context_snippet=text[max(0, r.start-30):r.end+30],
                            strategy=r.strategy,
                            status="pending",
                        ))
                    session.add(PiiHold(
                        source_type="document", source_id=doc_id,
                        encrypted_text=text,
                        status="pending",
                    ))
                    self._save_document(doc_id, user_id, kb_id, filename, 0, "pending_review")
                    session.commit()
                finally:
                    session.close()
                return {
                    "document_id": doc_id,
                    "filename": filename,
                    "status": "pending_review",
                    "chunk_count": 0,
                    "message": "文档因包含禁止上传的敏感内容，已暂停处理，管理员审核中",
                }
            text = mask_text(text)

        sections = document_structurer.structure(text)
        chunks: list[Chunk] = text_chunker.chunk(sections)

        doc_id = new_id()
        if not chunks:
            self._save_document(doc_id, user_id, kb_id, filename, 0, "failed")
            return {"document_id": doc_id, "chunk_count": 0, "status": "failed"}

        chunks = chunk_metadata_generator.generate(chunks)
        embeddings = sf_embedding.embed_batch([c.text for c in chunks])

        chunk_ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        chunks_data = [
            {
                "chunk_id": chunk_ids[i],
                "document_id": doc_id,
                "kb_id": kb_id,
                "text": c.text,
                "embedding": embeddings[i],
                "title": c.title,
                "summary": c.summary,
                "questions": "; ".join(c.questions),
                "section_path": " > ".join(c.section_path) if c.section_path else "",
                "search_text": tokenize(c.text),
                "visibility": visibility,
                "allowed_roles": allowed_roles or [],
            }
            for i, c in enumerate(chunks)
        ]

        pgvector_store.add_chunks(chunks_data)
        self._save_document(doc_id, user_id, kb_id, filename, len(chunks), "indexed")

        return {
            "document_id": doc_id,
            "filename": filename,
            "status": "indexed",
            "chunk_count": len(chunks),
        }

    def _save_document(self, doc_id: str, user_id: str, kb_id: str,
                       filename: str, chunk_count: int, status: str):
        session = get_session()
        try:
            session.add(Document(
                document_id=doc_id,
                kb_id=kb_id,
                filename=filename,
                owner_id=user_id,
                status=status,
                chunk_count=chunk_count,
                created_at=utc_now(),
            ))
            session.commit()
        finally:
            session.close()


document_indexer = DocumentIndexer()
