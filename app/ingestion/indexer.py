"""Full indexing pipeline: parse → clean → structure → chunk → metadata → embed → store.

Supports incremental update: reuses chunks by content_hash to avoid redundant
embedding and LLM calls. Coordinates all ingestion stages and persists results
to PostgreSQL + pgvector.
"""

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import re
import time

from app.config import settings
from app.core.doc_relation import cross_doc_builder
from app.ingestion.chunker import Chunk, text_chunker
from app.ingestion.cleaner import document_cleaner
from app.ingestion.embedding_text import build_embedding_text  # noqa: F401  # 保留供 ablation
from app.ingestion.metadata import chunk_metadata_generator
from app.ingestion.structurer import document_structurer
from app.llm.embedding import sf_embedding
from app.store import pgvector_store
from app.store.db import Document, get_db_ctx, get_session, new_id, utc_now
from app.store.pgvector_store import tokenize

logger = logging.getLogger(__name__)
_INDEX_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _extract_year_from_filename(filename: str) -> str | None:
    """从文件名提取年份标签，用于 chunk 的时序标注。

    匹配模式：
    - "三一重工_2023年年度报告.pdf" → "2023年"
    - "2024_annual_report.pdf" → "2024年"
    - "Q1_2025_report.docx" → "2025年"
    """
    m = re.search(r"((?:19|20)\d{2})\s*年?", filename)
    if m:
        return f"{m.group(1)}年"
    return None


def _extract_year_from_content(text: str) -> str | None:
    """从文档正文提取年份（多源兜底）。

    适用场景：文件名不含年份（如 "annual_report.pdf"），但文档第一页有 "2023 年"
    等模式。扫描文本前 1500 字（通常包含报告封面/标题），匹配多个常见模式：
    - "2023年年度报告" / "2023 年度报告"
    - "本报告期：2023 年" / "报告年度：2023"
    - "2023 年 12 月 31 日"（用最新出现的年份）

    Returns: "YYYY年" 或 None
    """
    if not text:
        return None
    head = text[:1500]

    # 优先匹配明确的"年度报告"形式（最高置信度）
    patterns = [
        r"((?:19|20)\d{2})\s*年(?:度报告|年度报告|年报)",
        r"报告(?:期|年度)\s*[::]\s*((?:19|20)\d{2})",
        r"((?:19|20)\d{2})\s*年\s*报告",
        r"((?:19|20)\d{2})\s*年\s*\d+\s*月",  # 2023 年 12 月
    ]
    for pat in patterns:
        m = re.search(pat, head)
        if m:
            return f"{m.group(1)}年"

    # 兜底：找第一个 1900-2099 之间的年份
    m = re.search(r"((?:19|20)\d{2})", head)
    if m:
        return f"{m.group(1)}年"
    return None


def extract_document_year(filename: str, content_text: str) -> str | None:
    """综合多源提取文档年份。

    优先级：文件名 > 内容（多源兜底，确保通用性）。
    适用于任何类型文档——年报、API 文档、用户手册等。
    """
    year = _extract_year_from_filename(filename)
    if year:
        return year
    return _extract_year_from_content(content_text)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _collect_future_pair(meta_fut, embed_fut):
    """设计审查 P0-5：成对取并行 future 结果；任一失败时取消并吞掉另一个。

    旧实现 `_meta_fut.result()` 抛异常时 `_embed_fut` 不被 await/cancel，
    embedding 线程悬空、异常静默丢失。这里保证异常路径下另一个 future
    也被收尾（cancel 未启动的 / exception() 等待跑完），不悬空线程。
    """
    try:
        return meta_fut.result(), embed_fut.result()
    except BaseException:
        for fut in (meta_fut, embed_fut):
            if not fut.done():
                fut.cancel()
        for fut in (meta_fut, embed_fut):
            try:
                fut.exception()
            except BaseException:
                pass
        raise


# ── 摄入进度事件：user 定向 + 节流 ─────────────────────────
# 旧实现每个 chunk 向所有 SSE 订阅者广播一次（500 块 = 500 条 × 全员），
# 还会把别人文档的 id/状态/报错推给无关用户。现按 5% 桶节流 + 携带 user_id。
_progress_buckets: dict[str, int] = {}   # doc_id -> 上次放行的百分比桶


def _emit_progress(doc_id: str, user_id: str, embedded: int, total: int,
                   status: str, error_message: str = "") -> None:
    """发摄入进度。indexing 中间态按 5% 桶节流，终态必发并清桶。"""
    if status == "indexing":
        pct = int(embedded * 100 / total) if total else 0
        bucket = pct // 5
        if _progress_buckets.get(doc_id) == bucket:
            return
        _progress_buckets[doc_id] = bucket
    else:
        _progress_buckets.pop(doc_id, None)
    try:
        from app.api.documents import emit_doc_progress
        payload = {
            "document_id": doc_id,
            "embedded_chunk_count": embedded,
            "chunk_count": total,
            "status": status,
            "user_id": user_id,
        }
        if error_message:
            payload["error_message"] = error_message
        emit_doc_progress(payload)
    except Exception:
        pass


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
            _emit_progress(doc_id, user_id, 0, 0, "failed", "解析/清洗失败")
            return {
                "document_id": doc_id,
                "filename": filename,
                "status": "failed",
                "chunk_count": 0,
            }
        pii_findings_cache = None  # cache PII scan to avoid 3x pass
        if settings.pii_enabled:
            from app.core.pii_scanner import mask_text, scan, scan_and_reject
            rejects = scan_and_reject(text)
            if rejects:
                logger.info("ingest.pii_rejected doc=%s rule_count=%d", u_tag, len(rejects))
                return self._reject_document(text, rejects, user_id, kb_id, filename)
            pii_findings_cache = scan(text)
            text = mask_text(text, findings=pii_findings_cache)
            logger.debug("ingest.pii_masked doc=%s mask_count=%d", u_tag, len(pii_findings_cache))

        # 设计审查 P0-6：content_hash 必须基于脱敏后文本——存储内容变了 hash 才变，
        # PII 规则变更后重传才会真正重索引。旧顺序（先 hash 再 mask）会跳过重传。
        doc_hash = _content_hash(text)

        existing = None
        if document_id:
            with get_db_ctx() as session:
                existing = session.query(Document).filter(
                    Document.document_id == document_id
                ).first()
            # 只有 indexed 算健康终态：failed/partial/indexing 的文档
            # 用相同内容重试必须真正重索引，旧逻辑只比 hash 会永远卡死
            if (existing and existing.content_hash == doc_hash
                    and existing.status == "indexed"):
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

        # 多源提取文档年份（文件名优先，内容兜底），加到 chunk 的 section_path 前面
        doc_year = extract_document_year(filename, text)
        if doc_year:
            for chunk in chunks:
                # 过滤空字符串避免 "2023年 >  > 标题" 这种空路径
                chunk.section_path = [doc_year] + [p for p in chunk.section_path if p]

        logger.info(
            "ingest.chunked doc=%s sections=%d chunks=%d elapsed_ms=%.1f",
            u_tag, len(sections), len(chunks), (time.monotonic() - t_chunk) * 1000,
        )

        if not chunks:
            doc_id = document_id or new_id()
            self._save_document(doc_id, user_id, kb_id, filename, 0, "failed", doc_hash)
            _emit_progress(doc_id, user_id, 0, 0, "failed", "文档切块后为空")
            return {"document_id": doc_id, "chunk_count": 0, "status": "failed"}

        doc_id = document_id or new_id()

        # Document 行先于 chunks 落库：chunks 外键引用 documents，
        # 旧顺序（chunks 后才写 document）令 index(document_id=None) 必然 FK 违反
        self._save_document(doc_id, user_id, kb_id, filename, 0, "indexing", doc_hash)

        for c in chunks:
            c.content_hash = _content_hash(c.text)

        try:
            self._save_chunk_diag(doc_id, filename, sections, chunks)
        except OSError:
            logger.exception("chunk diag save failed (non-fatal) doc=%s", doc_id[:8])

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
            # Parallelize: metadata generation and chunk embedding are independent
            _meta_fut = _INDEX_POOL.submit(
                chunk_metadata_generator.generate, new_chunks
            )
            # 生产路径：embed(c.text)——保留 build_embedding_text 工具但当前不启用。
            # baseline-ablation 实证 build_embedding_text(c, doc) 加 document/section 前缀
            # 让 recall@10 1.000 → 0.984 / MRR 0.876 → 0.824
            # （docs/plans/2026-08-23-day2-morning-done.md），回滚 chunk-only embedding。
            # build_embedding_text + EMBEDDING_TEXT_VERSION 保留供未来 prefix 重新设计后启用。
            _embed_inputs = [c.text for c in new_chunks]
            _embed_fut = _INDEX_POOL.submit(
                lambda: asyncio.run(
                    sf_embedding.embed_with_fallback(_embed_inputs)
                )
            )
            new_chunks, embed_results = _collect_future_pair(_meta_fut, _embed_fut)
        else:
            embed_results = []
        new_idx = 0
        chunks_data = []
        question_source: list[tuple[str, list[str]]] = []
        seen_chunk_ids: dict[str, int] = {}
        embedded_count = 0
        error_messages: list[str] = []

        for i, (c, is_reused) in enumerate(chunk_index):
            ch = c.content_hash
            # 稳定 chunk id：内容哈希派生——重索引时复用 chunk 保持原 id，
            # 其问题向量行不再因 seq 位移而孤儿化；同文档重复内容追加后缀
            base_id = f"{doc_id}_{ch[:10]}"
            n = seen_chunk_ids.get(base_id, 0)
            seen_chunk_ids[base_id] = n + 1
            chunk_id = base_id if n == 0 else f"{base_id}_{n}"

            if is_reused:
                old = old_chunks_map[ch]
                embedding = old["embedding"]
                search_text = old.get("search_text", "") or tokenize(c.text, stopwords=True)
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
                search_text = tokenize(c.text, stopwords=True)

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
                # embedding_text 字段保留（schema 已加列）——当前存 c.text 作为
                # "实际用于 embedding 的输入"的 audit。ablation 验证 build_embedding_text()
                # 加 prefix 反而恶化指标，所以 production 走 c.text。
                "embedding_text": c.text,
                "embedding_version": 1,
                # 复用 chunk 优先保留旧 LLM 元数据（chunker 的 section 标题不得覆盖之）
                "title": (is_reused and old.get("title")) or c.title or "",
                "summary": (is_reused and old.get("summary")) or c.summary or "",
                "questions": "; ".join(c.questions) if c.questions else (is_reused and old.get("questions", "") or ""),
                "section_path": " > ".join(c.section_path) if c.section_path else "",
                "search_text": search_text,
                "content_hash": ch,
                "visibility": visibility,
                "allowed_roles": allowed_roles or [],
            })
            # questions 与 chunk_id 在构造点绑定——后续不再依赖 zip(chunks, chunks_data)
            if not is_reused:
                question_source.append((chunk_id, list(c.questions or [])))

            _emit_progress(doc_id, user_id, embedded_count, len(chunk_index), "indexing")

            if not is_reused and i % 10 == 0 and i > 0:
                self._save_document(doc_id, user_id, kb_id, filename, len(chunks), "indexing", doc_hash,
                                    embedded_chunk_count=embedded_count, error_message="; ".join(error_messages[-3:]))

        total_new = len(chunk_index) - len(old_chunks_map)
        failed_count = total_new - (embedded_count - len(old_chunks_map))
        final_error = "; ".join(error_messages[:3]) if error_messages else ""

        if embedded_count == 0 and not old_chunks_map:
            self._save_document(doc_id, user_id, kb_id, filename, 0, "failed", doc_hash,
                                embedded_chunk_count=0, error_message=final_error or "所有分块向量化均失败")
            _emit_progress(doc_id, user_id, 0, len(chunks), "failed",
                           final_error or "所有分块向量化均失败")
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
        # 部分/全部新 chunk embedding 失败 + 有旧索引：保留旧索引 + failed。
        # 旧逻辑只挡「全部新失败」，对「部分失败」→ diff upsert 把失败新块
        # 对应的旧行删掉 → 静默丢内容。新逻辑统一处理：只要任一新块失败
        # 且文档已索引过，就宁可保留旧版本不动，让用户重试恢复——
        # 正确性优先于可用性，重试复用 hash 成本低。新文档首摄允许 partial
        # （无可丢的旧内容）。
        if old_chunks_map and error_messages:
            msg = "保留旧索引，请重试：" + (final_error or "部分新增分块向量化失败")
            self._save_document(doc_id, user_id, kb_id, filename, len(chunks), "failed", doc_hash,
                                embedded_chunk_count=embedded_count,
                                error_message=msg)
            _emit_progress(doc_id, user_id, embedded_count, len(chunks), "failed", msg)
            return {
                "document_id": doc_id,
                "filename": filename,
                "status": "failed",
                "chunk_count": len(chunks),
                "message": msg,
            }
        try:
            if document_id:
                pgvector_store.replace_chunks(document_id, chunks_data)
            else:
                pgvector_store.add_chunks(chunks_data)

            # Embed and store chunk questions for multi-channel retrieval.
            # 用构造点绑定的 question_source——旧 zip(chunks, chunks_data)
            # 在 embedding 失败跳块后错位，会把问题挂到错误的 chunk
            question_data = []
            for cd_id, qs in question_source:
                for pos, q in enumerate(qs):
                    if q.strip():
                        question_data.append({
                            "chunk_id": cd_id,
                            "question": q,
                            "position": pos,
                        })
            if question_data:
                q_texts = [q["question"] for q in question_data]
                q_emb_results = asyncio.run(
                    sf_embedding.embed_with_fallback(q_texts)
                )
                valid_q = []
                for qd, (emb, err) in zip(question_data, q_emb_results):
                    if emb is not None:
                        qd["embedding"] = emb
                        valid_q.append(qd)
                fail_count = len(question_data) - len(valid_q)
                if fail_count:
                    logger.warning("ingest.questions_partial total=%d ok=%d fail=%d",
                                   len(question_data), len(valid_q), fail_count)
                if valid_q:
                    pgvector_store.upsert_chunk_questions(valid_q)
                    logger.info("ingest.questions_stored chunk=%d questions=%d ok=%d",
                                len(chunks), len(question_data), len(valid_q))

            # 清理孤儿问题行：chunk 删除/历史 id 遗留后兜底
            pgvector_store.delete_orphan_chunk_questions(
                doc_id, [cd["chunk_id"] for cd in chunks_data])

            self._save_document(doc_id, user_id, kb_id, filename, len(chunks), status, doc_hash,
                                embedded_chunk_count=embedded_count, error_message=final_error)
            _emit_progress(doc_id, user_id, embedded_count, len(chunks), status, final_error)
        except Exception:
            logger.exception("Failed to persist chunks/document for doc_id=%s", doc_id)
            _emit_progress(doc_id, user_id, embedded_count, len(chunks), "failed",
                           "持久化失败(详见日志)")
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
