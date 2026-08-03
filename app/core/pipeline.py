from app.core.memory import conversation_memory
from app.core.rewrite import query_rewrite_service
from app.core.intent import intent_classifier
from app.core.retrieval import retrieval_engine
from app.core.prompt import prompt_builder
from app.core.diagnostics import DiagContext
from app.llm.chat import minimax_client
from app.llm.base import CircuitOpenError, provider_health
from app.core.doc_relation import cross_doc_synthesizer
from app.core.tag_parser import TagStreamParser
from app.store import pgvector_store
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


def _resolve_doc_map(chunks: list[RetrievedChunk]) -> dict[str, str]:
    """document_id → filename 映射，供 sources 组装与跨文档标注共用。"""
    doc_map: dict[str, str] = {}
    doc_ids = list({c.document_id for c in chunks if c.document_id})
    if doc_ids:
        from app.store.db import get_db_ctx, Document
        with get_db_ctx() as session:
            rows = session.query(Document.document_id, Document.filename).filter(
                Document.document_id.in_(doc_ids)
            ).all()
            for row in rows:
                doc_map[row.document_id] = row.filename
    return doc_map


def _build_sources(chunks: list[RetrievedChunk], doc_map: dict[str, str]) -> list[SourceInfo]:
    """Build SourceInfo list for frontend. 必须在跨文档合并去重之后调用，
    保证 [Source N] 编号与 UI 来源卡片、prompt 内编号三者一致。"""
    if not chunks:
        return []
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

_EOL = chr(10)
_EOL2 = chr(10) * 2

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
    # 不用裸单字 其/该/他/她——会误报 其他/其实/尤其/该文件 等常见词，
    # 给无代词查询强加一次 LLM 改写
    if re.search(r"(它|他们|她们|它们|这个|那个|这些|那些|这位|那位|上述|前面|上文)", query):
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
        if not all_kb_ids:
            # 默认全库路由：旧实现传 None 会让意图分类器短路，路由从未真正工作
            all_kb_ids = await asyncio.to_thread(pgvector_store.list_kb_ids)

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

        # 先解析文件名；sources 事件延迟到跨文档合并去重之后发出，
        # 否则按文档合并会使 [Source N] 编号整体位移，与 UI 来源卡片对不上
        doc_map = _resolve_doc_map(unique_chunks)

        # Cross-doc synthesis: group chunks by document, annotate texts with source
        doc_ids_in_result = list({c.document_id for c in unique_chunks if c.document_id})
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
            for g in doc_groups:
                g["filename"] = doc_map.get(g["document_id"], g["filename"])
            yield f"event: cross_doc\ndata: {json.dumps(doc_groups)}\n\n"

        sources = _build_sources(unique_chunks, doc_map)
        yield f"event: sources\ndata: {json.dumps([s.model_dump() for s in sources])}\n\n"

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
        stream_start = time.monotonic()
        first_token = True
        chat_degraded = False

        parser = TagStreamParser()

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
                for evt in parser.feed(raw_token):
                    evt_text = evt["text"]
                    if not evt_text:
                        continue
                    if evt["kind"] == "thinking":
                        yield "event: thinking" + _EOL + "data: " + _sse_safe(evt_text) + _EOL2
                    else:
                        yield "event: token" + _EOL + "data: " + _sse_safe(_norm(evt_text)) + _EOL2

        except CircuitOpenError:
            chat_degraded = True
            import logging as _log
            _log.getLogger(__name__).warning("Chat circuit breaker open, returning degraded response")

        except GeneratorExit:
            # User interrupted or connection lost
            if parser.answer_text or parser.thinking_text:
                await conversation_memory.add_message(
                    conv_id, "assistant",
                    _pii_safe(_norm(parser.answer_text)),
                    thinking_content=_pii_safe(_norm(parser.thinking_text)) if parser.thinking_text else None,
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
            if parser.answer_text or parser.thinking_text:
                await conversation_memory.add_message(
                    conv_id, "assistant",
                    _pii_safe(_norm(parser.answer_text)),
                    thinking_content=_pii_safe(_norm(parser.thinking_text)) if parser.thinking_text else None,
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

        # Normal completion —— 排空解析器缓冲（展示与持久化同源于事件流）
        for evt in parser.flush():
            evt_text = evt["text"]
            if not evt_text:
                continue
            if evt["kind"] == "thinking":
                yield "event: thinking" + _EOL + "data: " + _sse_safe(evt_text) + _EOL2
            else:
                yield "event: token" + _EOL + "data: " + _sse_safe(_norm(evt_text)) + _EOL2
        answer_text = _norm(parser.answer_text)
        thinking_text = _norm(parser.thinking_text)
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
