from app.llm.chat import minimax_client
from app.models.schemas import IntentResult, IntentMatch
from app.config import settings
import json


INTENT_CLASSIFIER_PROMPT = """You are an intent classifier. Given a user question and a list of available knowledge bases, determine which knowledge bases are relevant.

Available knowledge bases:
{kb_list}

User question: {question}

Return a JSON object:
{{
  "intent_type": "KB",
  "matches": [
    {{"kb_id": "kb_name", "score": 0.95}}
  ]
}}

Rules:
- intent_type is always "KB"
- score between 0 and 1, higher means more relevant
- Only include knowledge bases with score >= 0.3
- Return at most {max_count} matches
- If no knowledge base is relevant, return empty matches array

Return only the JSON object, no other text."""


class IntentClassifier:
    def classify(self, question: str, kb_ids: list[str] | None = None) -> IntentResult:
        if not kb_ids:
            return IntentResult(sub_question=question, matches=[], intent_type="KB")

        kb_list_str = "\n".join(f"- {kid}" for kid in kb_ids)
        prompt = INTENT_CLASSIFIER_PROMPT.format(
            kb_list=kb_list_str,
            question=question,
            max_count=settings.max_intent_count,
        )
        result = minimax_client.chat([{"role": "user", "content": prompt}])
        try:
            data = json.loads(result.strip().removeprefix("```json").removesuffix("```").strip())
            matches = [IntentMatch(kb_id=m["kb_id"], score=m["score"]) for m in data.get("matches", [])]
            matches = [m for m in matches if m.score >= settings.intent_min_score]
            return IntentResult(
                sub_question=question,
                matches=matches[:settings.max_intent_count],
                intent_type=data.get("intent_type", "KB"),
            )
        except json.JSONDecodeError:
            return IntentResult(sub_question=question, matches=[], intent_type="KB")


intent_classifier = IntentClassifier()
