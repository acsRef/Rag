"""Query rewrite: pronoun resolution + sub-question splitting.

对用户问题做三件事:
  1. 代词消解:把"它/它们/这个/那个"等代词替换为对话摘要里的明确术语
     (例:"它的参数呢?" → "Transformer 注意力机制的参数是什么?")
  2. 子问题拆分:复合问题拆成多个可独立检索的子问题
  3. 独立化改写:让改写后的查询脱离对话历史仍可独立理解,适合直接送入 embedding 检索

输出 JSON:{"rewritten_query": "...", "sub_questions": ["...", "..."]}
LLM 解析失败时回退到原 query,不抛异常。
"""
from app.llm.chat import minimax_client
from app.llm.base import CircuitOpenError, PermanentError, TemporaryError, call_llm_with_retry, robust_json_parse
from app.models.schemas import RewriteResult
import logging

logger = logging.getLogger(__name__)


REWRITE_PROMPT = """你是一个查询改写助手。你的任务是将用户问题改写成自包含的检索查询，消除代词指代，必要时拆分子问题。

# 核心规则

【CRITICAL】改写后的查询必须能脱离对话历史独立理解。不含代词、不含模糊指代。
【CRITICAL】不要改变用户原始意图。不要添加问题中不存在的信息。违反将受罚。
【CRITICAL】返回格式必须是合法 JSON，只输出 JSON 对象，不包含任何其他文本。违反将受罚。

# 处理步骤

1. 检查用户问题中是否包含代词（如：它、它们、这个、那个、这些、那些、其、上述、该、此等）
2. 如果包含代词，通过对话摘要和最近历史确定所指代的具体概念，将其替换为明确的术语名称
3. 将消代后的问题改写为独立、完整、适合检索的自包含查询
4. 如果问题包含多个不同的子问题，将它们拆分开

# 边界处理

- 没有问题 → 直接原样返回
- 纯社交用语（你好、在吗、谢谢）→ 原样返回，sub_questions 只含原问题
- 问题很短但信息完整（如"什么是 RAG"）→ 保持原样，不需要展开
- 问题中的技术术语、版本号、专有名词必须原样保留，不得概括

# 示例

用户问题："如何实现它？"
对话摘要："用户询问了 Transformer 注意力机制的原理，已解释 QKV 计算方式"
输出：{{"rewritten_query": "如何用 PyTorch 实现 Transformer 注意力机制的 QKV 计算", "sub_questions": ["如何用 PyTorch 实现 Transformer 注意力机制的 QKV 计算"]}}

用户问题："上面的方法和基于向量的有什么区别？"
对话摘要："讨论了 RAG 的三种分块策略：固定大小分块、基于句子的分块和语义分块"
输出：{{"rewritten_query": "语义分块方法和基于向量的分块方法有什么区别", "sub_questions": ["语义分块方法和基于向量的分块方法有什么区别"]}}

用户问题："什么是 RAG？"
对话摘要：""
输出：{{"rewritten_query": "什么是 RAG", "sub_questions": ["什么是 RAG"]}}

对话摘要：
{summary}

最近对话：
{history}

用户问题：{question}

# 输出格式
{{"rewritten_query": "改写后的主查询", "sub_questions": ["子问题1", "子问题2", ...]}}

如果只有一个问题，sub_questions 中只包含改写后的查询即可。

# 输出前确认
□ 所有代词都已消除？
□ 改写后的查询能独立理解？
□ 没有引入原文不存在的信息？
□ JSON 格式正确？"""


class QueryRewriteService:
    async def rewrite(self, question: str, history: list[dict], summary: str = "", ctx=None) -> RewriteResult:
        """改写用户问题为自包含的检索查询。

        输入:当前问题 + 最近对话历史 + 已有摘要
        输出:`RewriteResult(rewritten_query, sub_questions)`

        行为:
          - 取最近 4 条历史消息进 prompt(避免上下文爆炸)
          - 若 LLM 返回非 JSON,降级为原 query(不抛异常,保证主流程不中断)
        """
        summary_str = summary if summary else "暂无对话摘要"
        history_str = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:]) if history else "暂无最近对话"
        prompt = REWRITE_PROMPT.format(summary=summary_str, history=history_str, question=question)
        try:
            result = await call_llm_with_retry(
                minimax_client.chat,
                [{"role": "user", "content": prompt}],
                tag="rewrite",
                max_retries=1,
            )
        except (CircuitOpenError, PermanentError, TemporaryError) as e:
            logger.warning("Rewrite LLM call failed (%s): %s", type(e).__name__, e)
            if ctx:
                ctx.track_error("rewrite", type(e).__name__, str(e), degraded=True)
            return RewriteResult(rewritten_query=question, sub_questions=[question])
        data = robust_json_parse(result)
        if data is None:
            logger.warning("Rewrite parse failed (first 200): %s", result[:200])
            if ctx:
                ctx.track_error("rewrite", "JSONDecodeError", "failed to parse LLM JSON output", degraded=True)
            return RewriteResult(rewritten_query=question, sub_questions=[question])
        return RewriteResult(
            rewritten_query=data.get("rewritten_query", question),
            sub_questions=data.get("sub_questions", [question]),
        )


query_rewrite_service = QueryRewriteService()
