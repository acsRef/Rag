from app.llm.chat import minimax_client
from app.models.schemas import RewriteResult
import json


REWRITE_PROMPT = """You are a query rewriting assistant. Given the conversation history and the latest user question, your task is to:
1. Rewrite the question to be self-contained and optimized for retrieval
2. If the question contains multiple distinct sub-questions, split them

Return a JSON object:
{
  "rewritten_query": "the rewritten main query",
  "sub_questions": ["sub question 1", "sub question 2", ...]
}

If there is only one question, sub_questions should contain just the rewritten query.

Conversation history:
{history}

User question: {question}

Return only the JSON object, no other text."""


class QueryRewriteService:
    def rewrite(self, question: str, history: list[dict]) -> RewriteResult:
        history_str = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:]) if history else "No history"
        prompt = REWRITE_PROMPT.format(history=history_str, question=question)
        result = minimax_client.chat([{"role": "user", "content": prompt}])
        try:
            data = json.loads(result.strip().removeprefix("```json").removesuffix("```").strip())
            return RewriteResult(
                rewritten_query=data.get("rewritten_query", question),
                sub_questions=data.get("sub_questions", [question]),
            )
        except json.JSONDecodeError:
            return RewriteResult(
                rewritten_query=question,
                sub_questions=[question],
            )


query_rewrite_service = QueryRewriteService()
