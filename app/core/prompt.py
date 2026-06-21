from app.models.schemas import RetrievedChunk

SYSTEM_PROMPT = "You are a helpful assistant. Answer the user's question based on the provided context and conversation history."


KB_ANSWER_TEMPLATE = """You are a knowledgeable assistant with access to a knowledge base. Answer the user's question based on the retrieved context.

Retrieved context:
{context}

{history}

User question: {query}

Answer concisely and accurately based on the context. If the context doesn't contain relevant information, say so politely."""


SYSTEM_ANSWER_TEMPLATE = """Answer the following question based on your own knowledge.

{history}

User question: {query}"""


class RAGPromptBuilder:
    def build_messages(
        self,
        query: str,
        history: list[dict],
        summary: str,
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[dict]:
        if not retrieved_chunks:
            return self._build_system_messages(query, history, summary)

        context_parts = []
        for i, chunk in enumerate(retrieved_chunks):
            context_parts.append(f"[Source {i+1}]\n{chunk.text}")

        context_str = "\n\n".join(context_parts)
        history_str = self._format_history(history, summary)

        user_prompt = KB_ANSWER_TEMPLATE.format(
            context=context_str,
            history=history_str,
            query=query,
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _build_system_messages(self, query: str, history: list[dict], summary: str) -> list[dict]:
        history_str = self._format_history(history, summary)
        user_prompt = SYSTEM_ANSWER_TEMPLATE.format(
            history=history_str,
            query=query,
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _format_history(self, history: list[dict], summary: str = "") -> str:
        parts = []
        if summary:
            parts.append(f"Conversation overview:\n{summary}\n")
        parts.append("Recent conversation:")
        for m in history:
            role = "User" if m["role"] == "user" else "Assistant"
            parts.append(f"{role}: {m['content']}")
        return "\n".join(parts)


prompt_builder = RAGPromptBuilder()
