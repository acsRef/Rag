"""Full indexing pipeline: parse → clean → structure → chunk → metadata → embed → store.

Supports incremental update: reuses chunks by content_hash to avoid redundant
embedding and LLM calls. Coordinates all ingestion stages and persists results
to PostgreSQL + pgvector.
"""

import asyncio
import hashlib
import json
import logging
import time

from app.store import pgvector_store
from app.llm.embedding import sf_embedding
from app.ingestion.chunker import text_chunker, Chunk
from app.ingestion.cleaner import document_cleaner
from app.ingestion.structurer import document_structurer
from app.ingestion.metadata import chunk_metadata_generator
from app.store.db import get_db_ctx, get_session, Document, new_id, utc_now
from app.store.pgvector_store import tokenize
from app.config import settings
from app.core.doc_relation import cross_doc_builder

logger = logging.getLogger(__name__)


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

        t_total = time.monotonic()
        u_tag = (document_id or "new")[:8]
        logger.info(
            "ingest.start doc=%s file=%s kb=%s reindex=%s",
            u_tag, filename, kb_id[:8], bool(document_id),
        )

        try:
            t_parse = time.monotonic()
            text = document_parser.parse_bytes(content, filename)
            text = document_cleaner.clean(text)
            logger.info(
                "ingest.parsed_cleaned doc=%s text_len=%d elapsed_ms=%.1f",
                u_tag, len(text), (time.monotonic() - t_parse) * 1000,
            )
        except Exception:
            logger.exception("Parse/clean failed for filename=%s", filename)
            doc_id = document_id or new_id()
            self._save_document(doc_id, user_id, kb_id, filename, 0, "failed", "")
            try:
                from app.api.documents import emit_doc_progress
                emit_doc_progress({
                    "document_id": doc_id,
                    "embedded_chunk_count": 0,
                    "chunk_count": 0,
                    "status": "failed",
                    "error_message": "解析/清洗失败",
                })
            except Exception:
                pass
            return {
                "document_id": doc_id,
                "filename": filename,
                "status": "failed",
                "chunk_count": 0,
            }
        doc_hash = _content_hash(text)

        pii_findings_cache = None  # cache PII scan to avoid 3x pass
        if settings.pii_enabled:
            from app.core.pii_scanner import scan, scan_and_reject, mask_text
            rejects = scan_and_reject(text)
            if rejects:
                logger.info("ingest.pii_rejected doc=%s rule_count=%d", u_tag, len(rejects))
                return self._reject_document(text, rejects, user_id, kb_id, filename)
            pii_findings_cache = scan(text)
            text = mask_text(text, findings=pii_findings_cache)
            logger.debug("ingest.pii_masked doc=%s mask_count=%d", u_tag, len(pii_findings_cache))

        existing = None
        if document_id:
            with get_db_ctx() as session:
                existing = session.query(Document).filter(
                    Document.document_id == document_id
                ).first()
            if existing and existing.content_hash == doc_hash:
                return {
                    "document_id": document_id,
                    "filename": filename,
                    "status": "unchanged",
                    "chunk_count": existing.chunk_count,
                    "message": "文档内容无变化，跳过索引",
                }

        t_chunk = time.monotonic()
        sections = document_structurer.structure(text)
        chunks: list[Chunk] = text_chunker.chunk(sections)
        logger.info(
            "ingest.chunked doc=%s sections=%d chunks=%d elapsed_ms=%.1f",
            u_tag, len(sections), len(chunks), (time.monotonic() - t_chunk) * 1000,
        )

        if not chunks:
            doc_id = document_id or new_id()
            self._save_document(doc_id, user_id, kb_id, filename, 0, "failed", doc_hash)
            try:
                from app.api.documents import emit_doc_progress
                emit_doc_progress({
                    "document_id": doc_id,
                    "embedded_chunk_count": 0,
                    "chunk_count": 0,
                    "status": "failed",
                    "error_message": "文档切块后为空",
                })
            except Exception:
                pass
            return {"document_id": doc_id, "chunk_count": 0, "status": "failed"}

        doc_id = document_id or new_id()

        for c in chunks:
            c.content_hash = _content_hash(c.text)

        self._save_chunk_diag(doc_id, filename, sections, chunks)

        old_chunks_map: dict[str, dict] = {}
        if document_id:
            for oc in pgvector_store.get_chunks_by_document(document_id):
                if oc.get("content_hash"):
                    old_chunks_map[oc["content_hash"]] = oc

        new_chunks: list[Chunk] = []
        chunk_index: list[tuple[Chunk, bool]] = []
        for c in chunks:
            if c.content_hash in old_chunks_map:
                chunk_index.append((c, True))
            else:
                chunk_index.append((c, False))
                new_chunks.append(c)
        reused_count = sum(1 for _, reused in chunk_index if reused)
        logger.info(
            "ingest.reuse_matched doc=%s reused=%d new=%d",
            u_tag, reused_count, len(new_chunks),
        )

        if new_chunks:
            new_chunks = chunk_metadata_generator.generate(new_chunks)
            embed_results = asyncio.run(
                sf_embedding.embed_with_fallback([c.text for c in new_chunks])
            )
        else:
            embed_results = []
        chunk_seq = 0
        new_idx = 0
        chunks_data = []
        embedded_count = 0
        error_messages: list[str] = []

        for i, (c, is_reused) in enumerate(chunk_index):
            ch = c.content_hash
            chunk_id = f"{doc_id}_{chunk_seq}"
            chunk_seq += 1

            if is_reused:
                old = old_chunks_map[ch]
                embedding = old["embedding"]
                search_text = old.get("search_text", "") or tokenize(c.text)
                embedded_count += 1
            else:
                if new_idx < len(embed_results):
                    embedding, err = embed_results[new_idx]
                    new_idx += 1
                else:
                    embedding, err = None, None
                if embedding is not None:
                    embedded_count += 1
                elif err:
                    error_messages.append(err)
                search_text = tokenize(c.text)

            # Skip chunks whose embedding permanently failed — they would be invisible
            # to vector search and storing NULL would cause pgvector issues.
            if not is_reused and embedding is None:
                logger.warning("ingest.skip_embedding_failed chunk=%s", chunk_id[:12])
                continue

            chunks_data.append({
                "chunk_id": chunk_id,
                "document_id": doc_id,
                "kb_id": kb_id,
                "text": c.text,
                "embedding": embedding,
                "title": c.title or (is_reused and old.get("title", "") or ""),
                "summary": c.summary or (is_reused and old.get("summary", "") or ""),
                "questions": "; ".join(c.questions) if c.questions else (is_reused and old.get("questions", "") or ""),
                "section_path": " > ".join(c.section_path) if c.section_path else "",
                "search_text": search_text,
                "content_hash": ch,
                "visibility": visibility,
                "allowed_roles": allowed_roles or [],
            })

            try:
                from app.api.documents import emit_doc_progress
                emit_doc_progress({
                    "document_id": doc_id,
                    "embedded_chunk_count": embedded_count,
                    "chunk_count": len(chunk_index),
                    "status": "indexing",
                })
            except Exception:
                pass

            if not is_reused and i % 10 == 0 and i > 0:
                self._save_document(doc_id, user_id, kb_id, filename, len(chunks), "indexing", doc_hash,
                                    embedded_chunk_count=embedded_count, error_message="; ".join(error_messages[-3:]))

        total_new = len(chunk_index) - len(old_chunks_map)
        failed_count = total_new - (embedded_count - len(old_chunks_map))
        final_error = "; ".join(error_messages[:3]) if error_messages else ""

        if embedded_count == 0 and not old_chunks_map:
            self._save_document(doc_id, user_id, kb_id, filename, 0, "failed", doc_hash,
                                embedded_chunk_count=0, error_message=final_error or "所有分块向量化均失败")
            try:
                from app.api.documents import emit_doc_progress
                emit_doc_progress({
                    "document_id": doc_id,
                    "embedded_chunk_count": 0,
                    "chunk_count": len(chunks),
                    "status": "failed",
                    "error_message": final_error or "所有分块向量化均失败",
                })
            except Exception:
                pass
            return {
                "document_id": doc_id,
                "filename": filename,
                "status": "failed",
                "chunk_count": 0,
            }

        status = "indexed" if failed_count == 0 else "partial"

        logger.info(
            "ingest.persisted doc=%s total=%d embedded=%d reused=%d status=%s total_elapsed_ms=%.1f",
            doc_id[:8], len(chunks), embedded_count, len(old_chunks_map),
            status, (time.monotonic() - t_total) * 1000,
        )
        try:
            if document_id:
                pgvector_store.replace_chunks(document_id, chunks_data)
            else:
                pgvector_store.add_chunks(chunks_data)
            self._save_document(doc_id, user_id, kb_id, filename, len(chunks), status, doc_hash,
                                embedded_chunk_count=embedded_count, error_message=final_error)
            try:
                from app.api.documents import emit_doc_progress
                emit_doc_progress({
                    "document_id": doc_id,
                    "embedded_chunk_count": embedded_count,
                    "chunk_count": len(chunks),
                    "status": status,
                    "error_message": final_error,
                })
            except Exception:
                pass
        except Exception:
            logger.exception("Failed to persist chunks/document for doc_id=%s", doc_id)
            try:
                from app.api.documents import emit_doc_progress
                emit_doc_progress({
                    "document_id": doc_id,
                    "embedded_chunk_count": embedded_count,
                    "chunk_count": len(chunks),
                    "status": "failed",
                    "error_message": "持久化失败(详见日志)",
                })
            except Exception:
                pass
            return {
                "document_id": doc_id,
                "filename": filename,
                "status": "failed",
                "chunk_count": 0,
            }

        try:
            cross_doc_builder.update_for_document(doc_id)
        except Exception:
            logger.exception("cross_doc.update_failed doc=%s", doc_id[:8])

        return {
            "document_id": doc_id,
            "filename": filename,
            "status": status,
            "chunk_count": len(chunks),
            "embedded_chunk_count": embedded_count,
            "message": final_error or "",
        }

    def _save_chunk_diag(self, doc_id: str, filename: str, sections: list, chunks: list[Chunk]) -> None:
        from app.core.diagnostics import DIAG_DIR
        diag_dir = DIAG_DIR / "chunks"
        diag_dir.mkdir(parents=True, exist_ok=True)
        path = diag_dir / f"{doc_id}.json"
        chunk_list = []
        for i, c in enumerate(chunks):
            chunk_list.append({
                "index": i,
                "text_preview": c.text[:400],
                "full_len": len(c.text),
                "title": c.title,
                "section_path": c.section_path,
                "content_hash": c.content_hash[:12],
            })
        section_list = []
        for s in sections:
            elem_list = []
            for e in s.elements:
                elem_list.append({
                    "type": e.type,
                    "is_atomic": e.is_atomic,
                    "len": len(e.text),
                    "text_preview": e.text[:100],
                })
            section_list.append({"title": s.title, "level": int(getattr(s, "level", 0)), "elements": elem_list})
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "document_id": doc_id,
                "filename": filename,
                "chunks": chunk_list,
                "sections": section_list,
            }, f, ensure_ascii=False, indent=2)
        logger.info("ingest.chunk_diag_saved doc=%s chunks=%d sections=%d", doc_id[:8], len(chunks), len(sections))

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
                       content_hash: str, embedded_chunk_count: int = 0,
                       error_message: str = ""):
        session = get_session()
        try:
            existing = session.query(Document).filter(
                Document.document_id == doc_id
            ).first()
            if existing:
                existing.status = status
                existing.chunk_count = chunk_count
                existing.embedded_chunk_count = embedded_chunk_count
                existing.error_message = error_message
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
                    embedded_chunk_count=embedded_chunk_count,
                    error_message=error_message,
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
