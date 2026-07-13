from app.core.memory import conversation_memory
from app.core.rewrite import query_rewrite_service
from app.core.intent import intent_classifier
from app.core.retrieval import retrieval_engine
from app.core.prompt import prompt_builder
from app.core.diagnostics import DiagContext
from app.llm.chat import minimax_client
from app.llm.base import CircuitOpenError, provider_health
from app.core.doc_relation import cross_doc_synthesizer
from app.models.schemas import ChatRequest, RetrievedChunk, SourceInfo
from app.config import settings
from typing import AsyncGenerator
import asyncio
import json
import logging
import re
import time


def _pii_safe(text: str) -> str:
    """Mask PII in text if PII filtering is enabled."""
    if not settings.pii_enabled:
        return text
    from app.core.pii_scanner import mask_text
    return mask_text(text)


def _build_sources(chunks: list[RetrievedChunk]) -> list[SourceInfo]:
    """Resolve document filenames and build SourceInfo list for frontend."""
    if not chunks:
        return []
    doc_ids = list({c.document_id for c in chunks if c.document_id})
    doc_map: dict[str, str] = {}
    if doc_ids:
        from app.store.db import get_db_ctx, Document
        with get_db_ctx() as session:
            rows = session.query(Document.document_id, Document.filename).filter(
                Document.document_id.in_(doc_ids)
            ).all()
            for row in rows:
                doc_map[row.document_id] = row.filename

    sources = []
    for c in chunks:
        text = c.text[:150].replace("\n", " ")
        sources.append(SourceInfo(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            filename=doc_map.get(c.document_id, ""),
            title=c.title,
            section_path=c.section_path,
            snippet=text,
            score=round(c.score, 4),
        ))
    return sources


_NL = "\\n"  # literal backslash-n for SSE JSON encoding


def _sse_safe(text: str) -> str:
    """Escape text for safe SSE data field (remove \r, encode \n)."""
    return text.replace(chr(10), _NL).replace(chr(13), "")

def _norm(text: str) -> str:
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse blank lines between consecutive list items
    text = re.sub(r'(\n\s*(?:[-*]|\d+\.)\s.+\n)\n+(?=\s*(?:[-*]|\d+\.)\s)', r'\1', text)
    return text.strip()
def _needs_decomposition(query: str) -> bool:
    """Return True if query needs sub-question decomposition and KB routing.

    Rules — any match → needs decomposition:
      1. Comparison / contrast patterns
      2. Multiple explicitly named entities (quoted terms)
      3. Reasoning / aggregation / multi-hop markers
      4. Anaphoric pronouns that need resolution
    """
    # Rule 1: comparison / contrast
    if re.search(r"(对比|比较|区别|差异|不同|哪个好|哪个更|vs\.?|versus)", query, re.IGNORECASE):
        return True
    if re.search(r"(和|与|跟|同).{1,20}(区别|不同|差异|对比)", query):
        return True

    # Rule 2: multiple explicit entities (quoted / 《》书名号)
    entities = re.findall(r"《[^》]+》|\"[^\"]+\"|'[^']+'", query)
    if len(entities) >= 2:
        return True

    # Rule 3: reasoning / aggregation / multi-hop markers
    if re.search(r"(最多|最少|最高|最低|哪个|哪家|谁是|谁在)", query):
        return True
    if re.search(r"(为什么|为何|原因|因素|影响|如何导致)", query):
        return True
    if re.search(r"(总结|汇总|概括|整体|全年|整个|所有)", query):
        return True

    # Rule 4: anaphoric pronouns (need resolution from context)
    if re.search(r"(它|他|她|这个|那个|这些|那些|其|该|上述|前面)", query):
        return True

    return False


class RAGPipeline:
    async def execute(
        self,
        req: ChatRequest,
        user_id: str = "anonymous",
        user_role_ids: list[int] | None = None,
        can_read_all: bool = False,
        ctx: DiagContext | None = None,
    ) -> AsyncGenerator[str, None]:
        conv_id = await asyncio.to_thread(
            conversation_memory.get_or_create_conversation,
            req.conversation_id, user_id,
        )
        yield f"event: metadata\ndata: {json.dumps({'conversation_id': conv_id})}\n\n"

        if ctx is None and settings.diagnostics_enabled:
            ctx = DiagContext(query=req.query)
            ctx.conversation_id = conv_id

        if settings.pii_enabled:
            from app.core.pii_scanner import scan_and_reject
            rejects = scan_and_reject(req.query)
            if rejects:
                from app.store.db import get_session, PiiAlert
                session = None
                try:
                    session = get_session()
                    for r in rejects:
                        session.add(PiiAlert(
                            source_type="chat", source_id=conv_id,
                            rule_name=r.rule_name, matched_text=r.matched_text,
                            context_snippet=req.query[max(0, r.start-30):r.end+30],
                            strategy=r.strategy, status="pending",
                        ))
                    session.commit()
                finally:
                    if session:
                        session.close()
                if ctx:
                    ctx.record("rejected", reason="pii", details=[r.rule_name for r in rejects])
                    ctx.save()
                yield "event: error\ndata: {\"error\":\"您的问题涉及敏感信息，无法回答，请修改后重试\"}\n\n"
                yield "event: done\ndata: {}\n\n"
                return

        history = await asyncio.to_thread(conversation_memory.get_history, conv_id)
        summary = await asyncio.to_thread(conversation_memory.get_summary, conv_id)
        all_kb_ids = req.knowledge_base_ids

        needs_decomp = _needs_decomposition(req.query)
        if not needs_decomp:
            # Fast path: no LLM rewrite/intent, search all KBs directly
            sub_queries = [req.query]
        else:
            rewrite_result = await query_rewrite_service.rewrite(req.query, history, summary, ctx=ctx)
            if ctx:
                ctx.record("rewrite",
                    original=req.query,
                    rewritten=rewrite_result.rewritten_query,
                    sub_questions=rewrite_result.sub_questions,
                )
            sub_queries = rewrite_result.sub_questions

        # --- Retrieve ---
        yield "event: status\ndata: {\"phase\":\"retrieving\",\"message\":\"正在检索知识库...\"}\n\n"
        all_chunks: list[RetrievedChunk] = []

        async def _retrieve_one(sub_q: str) -> list[RetrievedChunk]:
            intent = None
            if needs_decomp:
                intent = await intent_classifier.classify(sub_q, all_kb_ids, ctx=ctx)
                if ctx:
                    ctx.append("intent", {
                        "sub_query": sub_q,
                        "kbs": [
                            {"name": m.kb_id, "kb_id": m.kb_id, "confidence": m.score}
                            for m in (intent.matches or [])
                        ],
                        "intent_type": intent.intent_type,
                    })
            try:
                return await retrieval_engine.retrieve(
                    sub_q, intent,
                    user_role_ids=user_role_ids,
                    can_read_all=can_read_all,
                    ctx=ctx,
                )
            except Exception:
                logging.getLogger(__name__).exception("retrieve.sub_query_failed q=%s", sub_q[:40])
                return []

        for i, sub_q in enumerate(sub_queries):
            yield f"event: status\ndata: {json.dumps({'phase':'retrieving','message':f'正在检索子问题 ({i+1}/{len(sub_queries)})...'})}\n\n"

        if len(sub_queries) > 1:
            results_list = await asyncio.gather(*[_retrieve_one(q) for q in sub_queries])
        else:
            results_list = [await _retrieve_one(sub_queries[0])]
        for chunks in results_list:
            all_chunks.extend(chunks)

        if not all_chunks:
            try:
                chunks = await retrieval_engine.retrieve(
                    req.query, None,
                    user_role_ids=user_role_ids,
                    can_read_all=can_read_all,
                    ctx=ctx,
                )
            except Exception:
                chunks = []
            all_chunks.extend(chunks)

        # Dedup + sort
        seen = set()
        unique_chunks = []
        for c in all_chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                unique_chunks.append(c)
        unique_chunks.sort(key=lambda x: x.score, reverse=True)
        unique_chunks = unique_chunks[:settings.rerank_top_k]

        # Context expansion: for each selected chunk, fetch ±N neighbor chunks
        # to provide surrounding context before feeding to LLM.
        _EXPAND_N = 2  # number of neighbors on each side
        cids = [c.chunk_id for c in unique_chunks]
        if cids:
            from app.store.pgvector_store import get_neighbor_chunks
            neighbors = get_neighbor_chunks(cids, expand_n=_EXPAND_N)
            for c in unique_chunks:
                nb = neighbors.get(c.chunk_id)
                if nb:
                    parts = []
                    if nb["before"]:
                        parts.append(nb["before"])
                    parts.append(c.text)
                    if nb["after"]:
                        parts.append(nb["after"])
                    c.text = "\n".join(parts)

        sources = _build_sources(unique_chunks)
        yield f"event: sources\ndata: {json.dumps([s.model_dump() for s in sources])}\n\n"

        # Cross-doc synthesis: group chunks by document, annotate texts with source
        doc_ids_in_result = list({s.document_id for s in sources if s.document_id})
        if len(doc_ids_in_result) > 1:
            annotated_texts, doc_groups = cross_doc_synthesizer.synthesize_texts(unique_chunks)
            text_map = {g["document_id"]: at for g, at in zip(doc_groups, annotated_texts)}
            deduped = []
            seen_docs = set()
            for c in unique_chunks:
                if c.document_id in text_map:
                    if c.document_id not in seen_docs:
                        seen_docs.add(c.document_id)
                        c.text = text_map[c.document_id]
                        deduped.append(c)
                else:
                    deduped.append(c)
            unique_chunks = deduped
            yield f"event: cross_doc\ndata: {json.dumps(doc_groups)}\n\n"

        messages = prompt_builder.build_messages(
            query=req.query,
            history=history,
            summary=summary,
            retrieved_chunks=unique_chunks,
        )

        if ctx:
            ctx.record("topk", chunks=[
                dict(chunk_id=c.chunk_id, document_id=c.document_id, title=c.title,
                     section_path=c.section_path, score=round(c.score, 4),
                     source=sources[i].filename if i < len(sources) else "",
                     text_preview=c.text[:200])
                for i, c in enumerate(unique_chunks)
            ])
            total_chars = sum(len(m.get("content", "")) for m in messages)
            ctx.record("prompt",
                system_prompt_chars=len(messages[0].get("content", "")) if messages else 0,
                total_chars=total_chars,
                message_count=len(messages),
                topk_chars=sum(len(c.text) for c in unique_chunks),
            )

        # --- Stream LLM ---
        await conversation_memory.add_message(
            conv_id, "user", _pii_safe(req.query),
            status="completed", user_id=user_id,
        )

        yield "event: status\ndata: {\"phase\":\"thinking\",\"message\":\"AI 正在思考...\"}\n\n"

        full_buffer = ""      # raw accumulation for diagnostics
        thinking_text = ""    # content inside <think>...</think>
        answer_text = ""      # content outside <think> or inside <answer>
        stream_start = time.monotonic()
        first_token = True
        chat_degraded = False

        # Tag-state machine
        _STATE_NORMAL = 0
        _STATE_IN_THINK = 1
        _STATE_AFTER_THINK = 2
        tag_state = _STATE_NORMAL
        tag_buffer = ""  # short buffer for partial tag matching

        try:
            async for raw_token in minimax_client.chat_stream(
                messages,
                temperature=req.temperature,
                top_p=req.top_p,
            ):
                if first_token:
                    if ctx:
                        ctx.record("stream", first_token_ms=round((time.monotonic() - stream_start) * 1000, 1))
                    first_token = False

                full_buffer += raw_token
                tag_buffer += raw_token

                if tag_state == _STATE_NORMAL:
                    idx = tag_buffer.find("<think>")
                    if idx >= 0:
                        # Flush text before <think> as normal token
                        before = tag_buffer[:idx]
                        if before.strip():
                            answer_text += before
                            yield f"event: token\ndata: {_sse_safe(_norm(before))}\n\n"
                        tag_buffer = tag_buffer[idx + 7:]  # skip "<think>"
                        tag_state = _STATE_IN_THINK
                    elif len(tag_buffer) > 60 and "<" not in tag_buffer[-8:]:
                        # No tag start possible — safe to emit (buffer 60+ chars so _norm has context)
                        answer_text += tag_buffer[:-3] if len(tag_buffer) > 3 else tag_buffer
                        chunk = tag_buffer
                        tag_buffer = ""
                        if chunk.strip():
                            yield f"event: token\ndata: {_sse_safe(_norm(chunk))}\n\n"
                    elif len(tag_buffer) > 120:
                        # Still buffering but it's long enough — force emit
                        answer_text += tag_buffer
                        chunk = tag_buffer
                        tag_buffer = ""
                        if chunk.strip():
                            yield f"event: token\ndata: {_sse_safe(_norm(chunk))}\n\n"

                elif tag_state == _STATE_IN_THINK:
                    idx = tag_buffer.find("</think>")
                    if idx >= 0:
                        think_chunk = tag_buffer[:idx]
                        thinking_text += think_chunk
                        if think_chunk.strip():
                            yield f"event: thinking\ndata: {_sse_safe(think_chunk)}\n\n"
                        tag_buffer = tag_buffer[idx + 8:]  # skip "</think>"
                        tag_state = _STATE_AFTER_THINK
                    elif len(tag_buffer) > 3 and "<" not in tag_buffer[-4:]:
                        # Safe to emit a chunk of thinking
                        emit_chunk = tag_buffer[:-2] if len(tag_buffer) > 2 else ""
                        thinking_text += emit_chunk
                        if emit_chunk.strip():
                            yield f"event: thinking\ndata: {_sse_safe(emit_chunk)}\n\n"
                        tag_buffer = tag_buffer[-2:] if len(tag_buffer) > 2 else tag_buffer

                elif tag_state == _STATE_AFTER_THINK:
                    # Everything after </think> is answer
                    answer_text += tag_buffer
                    if tag_buffer.strip():
                        yield f"event: token\ndata: {_sse_safe(_norm(tag_buffer))}\n\n"
                    tag_buffer = ""

        except CircuitOpenError:
            chat_degraded = True
            import logging as _log
            _log.getLogger(__name__).warning("Chat circuit breaker open, returning degraded response")

        except GeneratorExit:
            # User interrupted or connection lost
            if answer_text or thinking_text:
                await conversation_memory.add_message(
                    conv_id, "assistant",
                    _pii_safe(answer_text),
                    thinking_content=_pii_safe(thinking_text) if thinking_text else None,
                    status="interrupted",
                    user_id=user_id,
                )
            if ctx:
                ctx.update("stream", total_tokens=len(full_buffer),
                           total_ms=round((time.monotonic() - stream_start) * 1000, 1))
                ctx.save()
            return

        except Exception:
            import logging
            logging.getLogger(__name__).exception("Chat stream failed")
            chat_degraded = True
            if answer_text or thinking_text:
                await conversation_memory.add_message(
                    conv_id, "assistant",
                    _pii_safe(answer_text),
                    thinking_content=_pii_safe(thinking_text) if thinking_text else None,
                    status="interrupted",
                    user_id=user_id,
                )
            if ctx:
                ctx.update("stream", error="Chat stream failed",
                           total_tokens=len(full_buffer),
                           total_ms=round((time.monotonic() - stream_start) * 1000, 1))
                ctx.save()
            yield "event: error\ndata: {\"error\":\"生成回复时发生错误，请重试\"}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        # Normal completion — flush remaining buffer to correct target
        if tag_buffer:
            target = thinking_text if tag_state == _STATE_IN_THINK else answer_text
            target += tag_buffer
            tag_buffer = ""
        answer_text = _norm(answer_text)
        if thinking_text:
            thinking_text = _norm(thinking_text)
        if answer_text or (thinking_text and not answer_text):
            if not answer_text and thinking_text:
                answer_text = thinking_text
                thinking_text = ""
            await conversation_memory.add_message(
                conv_id, "assistant",
                _pii_safe(answer_text),
                thinking_content=_pii_safe(thinking_text) if thinking_text else None,
                status="completed",
                user_id=user_id,
            )
        elif chat_degraded:
            await conversation_memory.add_message(
                conv_id, "assistant",
                "抱歉，AI 服务暂时不可用，请稍后重试。您仍可浏览已上传的文档信息。",
                status="completed",
                user_id=user_id,
            )

        degraded_providers = provider_health.is_degraded()
        if settings.degradation_hint_enabled and degraded_providers:
            yield f"event: degraded\ndata: {json.dumps({'providers': degraded_providers})}\n\n"
            if ctx:
                ctx.record("degraded", providers=degraded_providers, chat_degraded=chat_degraded)

        # Emit stream status
        yield f"event: status\ndata: {json.dumps({'phase':'done','thinking_tokens':len(thinking_text),'answer_tokens':len(answer_text)})}\n\n"

        if ctx:
            ctx.update("stream", total_tokens=len(full_buffer),
                       total_ms=round((time.monotonic() - stream_start) * 1000, 1),
                       thinking_chars=len(thinking_text), answer_chars=len(answer_text))
            ctx.save()
        yield "event: done\ndata: {}\n\n"


rag_pipeline = RAGPipeline()
