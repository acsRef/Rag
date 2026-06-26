"""Intent classification: route a sub-question to 1-3 relevant knowledge bases.

调用方式:对每个 sub-question 调一次 `classify(question, kb_ids)`,
返回 `IntentResult.matches` 列表,每项含 `kb_id` + `score`。

过滤规则:
  - 仅保留 `score >= intent_min_score`(默认 0.35)的 KB
  - 最多返回 `max_intent_count`(默认 3)个 KB
  - 若 LLM 返回非 JSON → 降级为空 matches(不抛)
  - 若 `kb_ids` 为空 → 短路返回空 matches(整个系统进入全 KB 回退路径)

无意图命中时,上层 `RAGPipeline` 会把 query 撒向所有 KB 做兜底。
"""
from app.llm.chat import minimax_client
from app.models.schemas import IntentResult, IntentMatch
from app.config import settings
import logging
import json

logger = logging.getLogger(__name__)


INTENT_CLASSIFIER_PROMPT = """你是一个意图分类器。根据用户的问题和可用的知识库列表，判断哪些知识库与问题相关。

可用的知识库：
{kb_list}

用户问题：{question}

返回 JSON 对象：
{{
  "intent_type": "KB",
  "matches": [
    {{"kb_id": "知识库ID或名称", "score": 0.95}}
  ]
}}

规则：
- intent_type 固定为 "KB"
- score 范围 0~1，越高表示越相关
- 只保留 score >= 0.3 的知识库
- 最多返回 {max_count} 个匹配
- 如果没有相关知识库，返回空数组 matches
- 如果问题明显与技术/知识无关（如打招呼、闲聊），也返回空数组

示例：

用户问题："如何优化 RAG 分块策略？"
知识库：["文档处理", "系统配置"]
返回：{{"intent_type": "KB", "matches": [{{"kb_id": "文档处理", "score": 0.85}}]}}

用户问题："你好"
知识库：["文档处理", "系统配置"]
返回：{{"intent_type": "KB", "matches": []}}

只返回 JSON 对象，不要包含其他文本。"""


class IntentClassifier:
    async def classify(self, question: str, kb_ids: list[str] | None = None) -> IntentResult:
        """把 question 路由到最相关的 1-3 个 KB。

        输入:用户问题 + 可用 KB id 列表(由上层从 DB 读出)
        输出:`IntentResult(sub_question, matches, intent_type)`

        过滤:仅保留 `score >= intent_min_score` 的 KB,最多 `max_intent_count` 个。
        异常路径:LLM 返回非 JSON → 空 matches(上层兜底)。
        """
        if not kb_ids:
            return IntentResult(sub_question=question, matches=[], intent_type="KB")

        kb_list_str = "\n".join(f"- {kid}" for kid in kb_ids)
        prompt = INTENT_CLASSIFIER_PROMPT.format(
            kb_list=kb_list_str,
            question=question,
            max_count=settings.max_intent_count,
        )
        result = await minimax_client.chat([{"role": "user", "content": prompt}])
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
            logger.warning("Intent classification returned invalid JSON (first 200 chars): %s", result[:200])
            return IntentResult(sub_question=question, matches=[], intent_type="KB")


intent_classifier = IntentClassifier()
