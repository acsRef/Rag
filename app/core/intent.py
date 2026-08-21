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

import asyncio
import logging

from app.config import settings
from app.llm.base import (
    CircuitOpenError,
    PermanentError,
    TemporaryError,
    call_llm_with_retry,
    robust_json_parse,
)
from app.llm.chat import minimax_client
from app.models.schemas import IntentMatch, IntentResult

logger = logging.getLogger(__name__)


INTENT_CLASSIFIER_PROMPT = """你是一个知识库路由分类器，只做一件事：把用户问题映射到最相关的知识库 id。

【最高优先级 - 输出形态】
不要在内部思考，不要写任何思考过程、推理、解释、开场白或结束语。
你的【整个回复】只能是一个 JSON 对象，直接以 {{ 开头并以 }} 结束，前后没有任何其他字符。
任何解释、markdown 代码块、或"好的，让我..."之类的话都是错误。

正确示例（唯一允许的输出形态）：
{{"intent_type": "KB", "matches": [{{"kb_id": "docs-a", "score": 0.9}}]}}

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
□ 我的回复是否【只】是那个 JSON 对象、无任何其他字符？
□ 所有 KB ID 都来自输入列表？
□ 不相关的已返回空数组？
□ score 是否反映了真实相关度？"""


def _normalize_matches(
    raw,
    kb_ids: list[str],
    kb_names: dict[str, str],
) -> list[IntentMatch]:
    """归一 LLM 的 matches 输出：容忍畸形条目，名称反查 id。

    prompt 允许返回「知识库ID或名称」（示例全用名称）——旧实现把返回值直接
    当 kb_id 塞进 SQL，LLM 返回名称时 0 命中，且高分令全库兜底不触发，
    路由静默断链。缺键/错型条目一律丢弃，不再外抛 KeyError。
    """
    if not isinstance(raw, list):
        return []
    id_set = set(kb_ids)
    name_to_id = {name: kid for kid, name in kb_names.items() if name}
    out: list[IntentMatch] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        key = m.get("kb_id")
        if not isinstance(key, str) or not key.strip():
            continue
        key = key.strip()
        if key in id_set:
            kb_id = key
        elif key in name_to_id:
            kb_id = name_to_id[key]
        else:
            logger.warning("intent: LLM 返回未知 KB %r，丢弃该匹配", key[:40])
            continue
        try:
            score = float(m.get("score"))
        except (TypeError, ValueError):
            continue
        out.append(IntentMatch(kb_id=kb_id, score=score))
    return out


class IntentClassifier:
    async def classify(
        self, question: str, kb_ids: list[str] | None = None, ctx=None
    ) -> IntentResult:
        """把 question 路由到最相关的 1-3 个 KB。

        输入:用户问题 + 可用 KB id 列表(由上层从 DB 读出)
        输出:`IntentResult(sub_question, matches, intent_type)`

        过滤:仅保留 `score >= intent_min_score` 的 KB,最多 `max_intent_count` 个。
        异常路径:LLM 返回非 JSON → 空 matches(上层兜底)。
        """
        if not kb_ids:
            return IntentResult(sub_question=question, matches=[], intent_type="KB")

        # KB 名称与 id 一并给 LLM：旧实现只给 hex id，LLM 无从语义路由，
        # 意图分类形同虚设。名称解析失败时退回纯 id，不阻断主流程。
        kb_names: dict[str, str] = {}
        try:
            kb_names = await asyncio.to_thread(_resolve_kb_names, kb_ids)
            kb_list_str = "\n".join(f"- {kid}（{kb_names.get(kid, '未命名')}）" for kid in kb_ids)
        except Exception:
            logger.warning("intent: KB name resolution failed, using bare ids")
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
                model=settings.intent_model,
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
                ctx.track_error(
                    "intent", "JSONDecodeError", "failed to parse LLM JSON output", degraded=True
                )
            return IntentResult(sub_question=question, matches=[], intent_type="KB")
        matches = _normalize_matches(data.get("matches"), kb_ids, kb_names)
        matches = [m for m in matches if m.score >= settings.intent_min_score]
        return IntentResult(
            sub_question=question,
            matches=matches[: settings.max_intent_count],
            intent_type=data.get("intent_type", "KB"),
        )


def _resolve_kb_names(kb_ids: list[str]) -> dict[str, str]:
    """kb_id → 名称，供意图 prompt 使用（LLM 只对名称能做语义路由）。"""
    from app.store.db import KnowledgeBase, get_db_ctx

    with get_db_ctx() as session:
        rows = (
            session.query(KnowledgeBase.id, KnowledgeBase.name)
            .filter(KnowledgeBase.id.in_(kb_ids))
            .all()
        )
        return {r.id: r.name for r in rows}


intent_classifier = IntentClassifier()
