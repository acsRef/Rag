from app.models.schemas import RetrievedChunk
from app.config import settings
from app.core.pii_scanner import mask_text as _mask_text

SYSTEM_PROMPT = "你是一个智能助手。请根据提供的检索内容和对话历史回答用户的问题。如果信息不足，如实告知即可。"


KB_ANSWER_TEMPLATE = """你是一个知识库助手。请基于以下检索结果回答用户的问题。

检索内容：
{context}

{history}

用户问题：{query}

要求：
- 如果检索内容与问题无关或信息不足，请如实说"我没有找到相关信息"，不要编造
- 回答要简洁、准确
- 可以直接引用检索内容中的表述"""


SYSTEM_ANSWER_TEMPLATE = """请根据你自身的知识回答以下问题。

{history}

用户问题：{query}

要求：
- 如果你不知道答案，直接说"我不知道"，不要编造
- 回答要简洁、准确"""


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
            text = chunk.text
            if settings.pii_enabled:
                text = _mask_text(text)
            context_parts.append(f"[Source {i+1}]\n{text}")

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
