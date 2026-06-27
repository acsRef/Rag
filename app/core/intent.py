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
from app.llm.base import CircuitOpenError, PermanentError, TemporaryError, call_llm_with_retry, robust_json_parse
from app.models.schemas import IntentResult, IntentMatch
from app.config import settings
import logging

logger = logging.getLogger(__name__)


INTENT_CLASSIFIER_PROMPT = """你是一个知识库路由分类器。你的任务是将用户问题匹配到最相关的知识库。

# 核心规则

【CRITICAL】只从提供的知识库列表中做选择。不要编造不存在的知识库名称。
【CRITICAL】返回格式必须是合法 JSON，不得包含任何额外的文本、解释或包装。违反将受罚。
【CRITICAL】如果问题与所有知识库都不相关（闲聊、打招呼、无关话题），返回空 matches 数组。强行匹配不相关的知识库将受罚。

# 输入

可用的知识库：
{kb_list}

用户问题：{question}

# 输出格式

{{
  "intent_type": "KB",
  "matches": [
    {{"kb_id": "知识库ID或名称", "score": 0.95}}
  ]
}}

- intent_type: 固定为 "KB"
- score: 0~1 浮点数，越高越相关
- 只保留 score >= 0.3 的知识库
- 最多返回 {max_count} 个匹配
- 无匹配时返回空数组: {{"intent_type": "KB", "matches": []}}

# 示例

用户问题："如何优化 RAG 分块策略？"
知识库：["文档处理", "系统配置", "用户手册"]
输出：{{"intent_type": "KB", "matches": [{{"kb_id": "文档处理", "score": 0.85}}]}}

用户问题："帮我看看我的订单还在路上吗"
知识库：["产品文档", "API 文档", "运维手册"]
输出：{{"intent_type": "KB", "matches": []}}

用户问题："JWT 和 Session 鉴权有什么不同"
知识库：["安全指南", "开发规范", "用户手册"]
输出：{{"intent_type": "KB", "matches": [{{"kb_id": "安全指南", "score": 0.92}}, {{"kb_id": "开发规范", "score": 0.65}}]}}

# 输出前确认
□ 所有 KB ID 都来自输入列表？
□ JSON 格式正确，无多余文本？
□ 不相关的已返回空数组？
□ score 是否反映了真实相关度？"""


class IntentClassifier:
    async def classify(self, question: str, kb_ids: list[str] | None = None, ctx=None) -> IntentResult:
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
        try:
            result = await call_llm_with_retry(
                minimax_client.chat,
                [{"role": "user", "content": prompt}],
                tag="intent",
                max_retries=1,
            )
        except (CircuitOpenError, PermanentError, TemporaryError) as e:
            logger.warning("Intent LLM call failed (%s): %s", type(e).__name__, e)
            if ctx:
                ctx.track_error("intent", type(e).__name__, str(e), degraded=True)
            return IntentResult(sub_question=question, matches=[], intent_type="KB")
        data = robust_json_parse(result)
        if data is None:
            logger.warning("Intent parse failed (first 200): %s", result[:200])
            if ctx:
                ctx.track_error("intent", "JSONDecodeError", "failed to parse LLM JSON output", degraded=True)
            return IntentResult(sub_question=question, matches=[], intent_type="KB")
        matches = [IntentMatch(kb_id=m["kb_id"], score=m["score"]) for m in data.get("matches", [])]
        matches = [m for m in matches if m.score >= settings.intent_min_score]
        return IntentResult(
            sub_question=question,
            matches=matches[:settings.max_intent_count],
            intent_type=data.get("intent_type", "KB"),
        )


intent_classifier = IntentClassifier()
