from app.llm.chat import minimax_client
from app.models.schemas import RewriteResult
import json


REWRITE_PROMPT = """You are a query rewriting assistant. Given the conversation summary, recent history, and the latest user question, your task is to:

1. Identify any pronouns (it/they/this/that/these/those/its/their/them/其/它/它们/这个/那个/这些/那些/上述/该等) in the user question
2. Resolve each pronoun by looking up the conversation summary and recent history — replace it with the specific concrete term
3. Rewrite the resolved question to be self-contained and optimized for retrieval
4. If the question contains multiple distinct sub-questions, split them

Return a JSON object:
{
  "rewritten_query": "the rewritten main query",
  "sub_questions": ["sub question 1", "sub question 2", ...]
}

If there is only one question, sub_questions should contain just the rewritten query.

Conversation summary:
{summary}

Recent conversation:
{history}

User question: {question}

Return only the JSON object, no other text."""


class QueryRewriteService:
    def rewrite(self, question: str, history: list[dict], summary: str = "") -> RewriteResult:
        summary_str = summary if summary else "No summary available"
        history_str = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:]) if history else "No history"
        prompt = REWRITE_PROMPT.format(summary=summary_str, history=history_str, question=question)
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
