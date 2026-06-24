"""Full indexing pipeline: parse → clean → structure → chunk → metadata → embed → store.

Supports incremental update: reuses chunks by content_hash to avoid redundant
embedding and LLM calls. Coordinates all ingestion stages and persists results
to PostgreSQL + pgvector.
"""

import hashlib
import logging

from app.store import pgvector_store
from app.llm.embedding import sf_embedding
from app.ingestion.chunker import text_chunker, Chunk
from app.ingestion.cleaner import document_cleaner
from app.ingestion.structurer import document_structurer
from app.ingestion.metadata import chunk_metadata_generator
from app.store.db import get_session, Document, new_id, utc_now
from app.store.pgvector_store import tokenize
from app.config import settings


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DocumentIndexer:
    def index(
        self,
        filename: str,
        content: bytes,
        kb_id: str = "default",
        user_id: str = "default_user",
        visibility: str = "public",
        allowed_roles: list[int] | None = None,
        document_id: str | None = None,
    ) -> dict:
        from app.ingestion.parser import document_parser

        try:
            text = document_parser.parse_bytes(content, filename)
            text = document_cleaner.clean(text)
        except Exception:
            logger = logging.getLogger(__name__)
            logger.exception("Parse/clean failed for filename=%s", filename)
            doc_id = document_id or new_id()
            self._save_document(doc_id, user_id, kb_id, filename, 0, "failed", "")
            return {
                "document_id": doc_id,
                "filename": filename,
                "status": "failed",
                "chunk_count": 0,
            }
        doc_hash = _content_hash(text)

        # ── PII check ─────────────────────────────────
        if settings.pii_enabled:
            from app.core.pii_scanner import scan_and_reject, mask_text
            rejects = scan_and_reject(text)
            if rejects:
                return self._reject_document(
                    text, rejects, user_id, kb_id, filename,
                )
            text = mask_text(text)

        # ── Quick unchanged check ──────────────────────
        if document_id:
            session = get_session()
            try:
                existing = session.query(Document).filter(
                    Document.document_id == document_id
                ).first()
            finally:
                session.close()

            if existing and existing.content_hash == doc_hash:
                return {
                    "document_id": document_id,
                    "filename": filename,
                    "status": "unchanged",
                    "chunk_count": existing.chunk_count,
                    "message": "文档内容无变化，跳过索引",
                }

        # ── Structure + Chunk ──────────────────────────
        sections = document_structurer.structure(text)
        chunks: list[Chunk] = text_chunker.chunk(sections)

        if not chunks:
            doc_id = document_id or new_id()
            self._save_document(doc_id, user_id, kb_id, filename, 0, "failed", doc_hash)
            return {"document_id": doc_id, "chunk_count": 0, "status": "failed"}

        # ── Compute per-chunk hash for reuse ────────────
        for c in chunks:
            c.content_hash = _content_hash(c.text)

        # ── Load old chunks for reuse matching ──────────
        old_chunks_map: dict[str, dict] = {}
        if document_id:
            for oc in pgvector_store.get_chunks_by_document(document_id):
                if oc.get("content_hash"):
                    old_chunks_map[oc["content_hash"]] = oc

        # ── Separate reused vs new chunks ───────────────
        new_chunks: list[Chunk] = []
        chunk_index: list[tuple[Chunk, bool]] = []  # (chunk, is_reused)

        for c in chunks:
            if c.content_hash in old_chunks_map:
                chunk_index.append((c, True))
            else:
                chunk_index.append((c, False))
                new_chunks.append(c)

        # ── Process new chunks (embed + metadata) ───────
        new_embeddings: list = []
        if new_chunks:
            new_chunks = chunk_metadata_generator.generate(new_chunks)
            try:
                new_embeddings = sf_embedding.embed_batch([c.text for c in new_chunks])
            except Exception:
                logger = logging.getLogger(__name__)
                logger.exception("Embedding failed for filename=%s", filename)
                doc_id = document_id or new_id()
                self._save_document(doc_id, user_id, kb_id, filename, 0, "failed", doc_hash)
                return {
                    "document_id": doc_id,
                    "filename": filename,
                    "status": "failed",
                    "chunk_count": 0,
                }

        # ── Build final chunks_data ─────────────────────
        doc_id = document_id or new_id()
        chunk_seq = 0
        new_idx = 0
        chunks_data = []

        for c, is_reused in chunk_index:
            ch = c.content_hash
            chunk_id = f"{doc_id}_{chunk_seq}"
            chunk_seq += 1

            if is_reused:
                old = old_chunks_map[ch]
                chunks_data.append({
                    "chunk_id": chunk_id,
                    "document_id": doc_id,
                    "kb_id": kb_id,
                    "text": c.text,
                    "embedding": old["embedding"],
                    "title": old["title"],
                    "summary": old["summary"],
                    "questions": old["questions"],
                    "section_path": old.get("section_path", ""),
                    "search_text": old.get("search_text", "") or tokenize(c.text),
                    "content_hash": ch,
                    "visibility": visibility,
                    "allowed_roles": old.get("allowed_roles", allowed_roles or []),
                })
            else:
                if new_idx >= len(new_embeddings):
                    logger = logging.getLogger(__name__)
                    logger.error("Embedding count mismatch: %d chunks vs %d embeddings, skipping chunk %s",
                                 len(new_chunks), len(new_embeddings), chunk_id)
                    break
                chunks_data.append({
                    "chunk_id": chunk_id,
                    "document_id": doc_id,
                    "kb_id": kb_id,
                    "text": c.text,
                    "embedding": new_embeddings[new_idx],
                    "title": c.title,
                    "summary": c.summary,
                    "questions": "; ".join(c.questions),
                    "section_path": " > ".join(c.section_path) if c.section_path else "",
                    "search_text": tokenize(c.text),
                    "content_hash": ch,
                    "visibility": visibility,
                    "allowed_roles": allowed_roles or [],
                })
                new_idx += 1

        # ── Atomic replace ──────────────────────────────
        try:
            if document_id:
                pgvector_store.delete_chunks_by_document(document_id)

            pgvector_store.add_chunks(chunks_data)
            self._save_document(doc_id, user_id, kb_id, filename, len(chunks), "indexed", doc_hash)
        except Exception:
            logger = logging.getLogger(__name__)
            logger.exception("Failed to persist chunks/document for doc_id=%s", doc_id)
            return {
                "document_id": doc_id,
                "filename": filename,
                "status": "failed",
                "chunk_count": 0,
            }

        return {
            "document_id": doc_id,
            "filename": filename,
            "status": "indexed",
            "chunk_count": len(chunks),
        }

    def _reject_document(
        self, text: str, rejects: list, user_id: str, kb_id: str, filename: str,
    ) -> dict:
        from app.store.db import PiiAlert, PiiHold
        doc_id = new_id()
        session = get_session()
        try:
            for r in rejects:
                start = max(0, r.start)
                end = min(len(text), r.end)
                if end <= start:
                    end = start + 1
                ctx_start = max(0, start - 30)
                ctx_end = min(len(text), end + 30)
                session.add(PiiAlert(
                    source_type="document", source_id=doc_id,
                    rule_name=r.rule_name,
                    matched_text=r.matched_text,
                    context_snippet=text[ctx_start:ctx_end],
                    strategy=r.strategy,
                    status="pending",
                ))
            session.add(PiiHold(
                source_type="document", source_id=doc_id,
                content=text,
                status="pending",
            ))
            self._save_document(doc_id, user_id, kb_id, filename, 0, "pending_review", "")
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return {
            "document_id": doc_id,
            "filename": filename,
            "status": "pending_review",
            "chunk_count": 0,
            "message": "文档因包含禁止上传的敏感内容，已暂停处理，管理员审核中",
        }

    def _save_document(self, doc_id: str, user_id: str, kb_id: str,
                       filename: str, chunk_count: int, status: str,
                       content_hash: str):
        session = get_session()
        try:
            existing = session.query(Document).filter(
                Document.document_id == doc_id
            ).first()
            if existing:
                existing.status = status
                existing.chunk_count = chunk_count
                existing.filename = filename
                existing.content_hash = content_hash
                existing.updated_at = utc_now()
            else:
                session.add(Document(
                    document_id=doc_id,
                    kb_id=kb_id,
                    filename=filename,
                    owner_id=user_id,
                    status=status,
                    chunk_count=chunk_count,
                    content_hash=content_hash,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


document_indexer = DocumentIndexer()
