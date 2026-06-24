from app.core.memory import conversation_memory
from app.core.rewrite import query_rewrite_service
from app.core.intent import intent_classifier
from app.core.retrieval import retrieval_engine
from app.core.prompt import prompt_builder
from app.llm.chat import minimax_client
from app.models.schemas import ChatRequest, RetrievedChunk, SourceInfo
from app.config import settings
from typing import Generator
import json


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


class RAGPipeline:
    def execute(
        self,
        req: ChatRequest,
        user_id: str = "anonymous",
        user_role_ids: list[int] | None = None,
        can_read_all: bool = False,
    ) -> Generator[str, None, None]:
        conv_id = conversation_memory.get_or_create_conversation(
            req.conversation_id, user_id
        )
        yield f"event: metadata\ndata: {json.dumps({'conversation_id': conv_id})}\n\n"

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
                yield "event: error\ndata: {\"error\":\"您的问题涉及敏感信息，无法回答，请修改后重试\"}\n\n"
                yield "event: done\ndata: {}\n\n"
                return

        history = conversation_memory.get_history(conv_id)
        summary = conversation_memory.get_summary(conv_id)

        # Step 1: Rewrite query
        rewrite_result = query_rewrite_service.rewrite(req.query, history, summary)

        # Step 2: Classify intents for each sub-question
        all_chunks: list[RetrievedChunk] = []
        all_kb_ids = req.knowledge_base_ids
        has_retrieval = False
        for sub_q in rewrite_result.sub_questions:
            intent = intent_classifier.classify(sub_q, all_kb_ids)
            if intent.intent_type == "SYSTEM":
                continue
            has_retrieval = True
            chunks = retrieval_engine.retrieve(sub_q, intent, user_role_ids=user_role_ids, can_read_all=can_read_all)
            all_chunks.extend(chunks)

        if not has_retrieval:
            history = conversation_memory.get_history(conv_id)
            summary = conversation_memory.get_summary(conv_id)
            chunks = retrieval_engine.retrieve(req.query, None, user_role_ids=user_role_ids, can_read_all=can_read_all)
            all_chunks.extend(chunks)

        # Step 3: Deduplicate
        seen = set()
        unique_chunks = []
        for c in all_chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                unique_chunks.append(c)
        unique_chunks.sort(key=lambda x: x.score, reverse=True)
        unique_chunks = unique_chunks[:settings.rerank_top_k]

        # Step 4: Build sources and emit to frontend
        sources = _build_sources(unique_chunks)
        yield f"event: sources\ndata: {json.dumps([s.model_dump() for s in sources])}\n\n"

        # Step 5: Build messages
        messages = prompt_builder.build_messages(
            query=req.query,
            history=history,
            summary=summary,
            retrieved_chunks=unique_chunks,
        )

        # Step 5: Stream LLM response
        conversation_memory.add_message(conv_id, "user", _pii_safe(req.query), user_id)

        full_response = ""
        try:
            for token in minimax_client.chat_stream(
                messages,
                temperature=req.temperature,
                top_p=req.top_p,
            ):
                full_response += token
                safe_token = token.replace("\n", "\\n")
                yield f"event: token\ndata: {safe_token}\n\n"
        except GeneratorExit:
            if full_response:
                conversation_memory.add_message(conv_id, "assistant", _pii_safe(full_response), user_id)
            return
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Chat stream failed")
            if full_response:
                conversation_memory.add_message(conv_id, "assistant", _pii_safe(full_response), user_id)
            yield "event: error\ndata: {\"error\":\"生成回复时发生错误，请重试\"}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        conversation_memory.add_message(conv_id, "assistant", _pii_safe(full_response), user_id)
        yield "event: done\ndata: {}\n\n"


rag_pipeline = RAGPipeline()
