from app.core.memory import conversation_memory
from app.core.rewrite import query_rewrite_service
from app.core.intent import intent_classifier
from app.core.retrieval import retrieval_engine
from app.core.prompt import prompt_builder
from app.llm.chat import minimax_client
from app.models.schemas import ChatRequest, RetrievedChunk
from typing import Generator


class RAGPipeline:
    def execute(self, req: ChatRequest) -> Generator[str, None, None]:
        conv_id = conversation_memory.get_or_create_conversation(
            req.conversation_id, req.user_id
        )
        yield f"event: metadata\ndata: {{\"conversation_id\": \"{conv_id}\"}}\n\n"

        history = conversation_memory.get_history(conv_id)
        summary = conversation_memory.get_summary(conv_id)

        # Step 1: Rewrite query
        rewrite_result = query_rewrite_service.rewrite(req.query, history, summary)

        # Step 2: Classify intents for each sub-question
        all_chunks: list[RetrievedChunk] = []
        all_kb_ids = req.knowledge_base_ids
        for sub_q in rewrite_result.sub_questions:
            intent = intent_classifier.classify(sub_q, all_kb_ids)
            if intent.intent_type == "SYSTEM":
                continue
            chunks = retrieval_engine.retrieve(sub_q, intent)
            all_chunks.extend(chunks)

        # Step 3: Deduplicate
        seen = set()
        unique_chunks = []
        for c in all_chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                unique_chunks.append(c)
        unique_chunks.sort(key=lambda x: x.score, reverse=True)
        unique_chunks = unique_chunks[:5]

        # Step 4: Build messages
        messages = prompt_builder.build_messages(
            query=req.query,
            history=history,
            summary=summary,
            retrieved_chunks=unique_chunks,
        )

        # Step 5: Stream LLM response
        conversation_memory.add_message(conv_id, "user", req.query, req.user_id)

        full_response = ""
        for token in minimax_client.chat_stream(messages):
            full_response += token
            yield f"event: token\ndata: {token}\n\n"

        conversation_memory.add_message(conv_id, "assistant", full_response, req.user_id)

        yield "event: done\ndata: {}\n\n"


rag_pipeline = RAGPipeline()
