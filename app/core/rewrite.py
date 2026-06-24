from app.llm.chat import minimax_client
from app.models.schemas import RewriteResult
import logging
import json

logger = logging.getLogger(__name__)


REWRITE_PROMPT = """你是一个查询改写助手。请根据对话摘要、最近对话历史和当前用户问题，完成以下任务：

1. 检测用户问题中是否包含代词（如：它、它们、这个、那个、这些、那些、其、它的、它们的、上述、该、此等）
2. 如果包含代词，通过对话摘要和最近历史确定代词所指代的具体概念，将其替换为明确的术语名称
3. 将消代后的问题改写为独立、完整、适合检索的自包含查询
4. 如果问题包含多个不同的子问题，将它们拆分开

注意：
- 如果问题中没有代词，直接进入第 3 步改写
- 改写后的查询必须能脱离对话历史独立理解
- 不要改变用户的原始意图
- 不要添加问题中不存在的信息

示例：

用户问题："如何实现它？"
对话摘要："用户询问了 Transformer 注意力机制的原理，已解释 QKV 计算方式"
改写结果：{{"rewritten_query": "如何用 PyTorch 实现 Transformer 注意力机制的 QKV 计算", "sub_questions": ["如何用 PyTorch 实现 Transformer 注意力机制的 QKV 计算"]}}

用户问题："上面的方法和基于向量的有什么区别？"
对话摘要："讨论了 RAG 的三种分块策略：固定大小分块、基于句子的分块和语义分块"
改写结果：{{"rewritten_query": "语义分块方法和基于向量的分块方法有什么区别", "sub_questions": ["语义分块方法和基于向量的分块方法有什么区别"]}}

用户问题："什么是 RAG？"
对话摘要：""
改写结果：{{"rewritten_query": "什么是 RAG", "sub_questions": ["什么是 RAG"]}}

对话摘要：
{summary}

最近对话：
{history}

用户问题：{question}

请只返回 JSON 对象，不要包含其他文本：
{{"rewritten_query": "改写后的主查询", "sub_questions": ["子问题1", "子问题2", ...]}}

如果只有一个问题，sub_questions 中只包含改写后的查询即可。"""


class QueryRewriteService:
    def rewrite(self, question: str, history: list[dict], summary: str = "") -> RewriteResult:
        summary_str = summary if summary else "暂无对话摘要"
        history_str = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:]) if history else "暂无最近对话"
        prompt = REWRITE_PROMPT.format(summary=summary_str, history=history_str, question=question)
        result = minimax_client.chat([{"role": "user", "content": prompt}])
        try:
            data = json.loads(result.strip().removeprefix("```json").removesuffix("```").strip())
            return RewriteResult(
                rewritten_query=data.get("rewritten_query", question),
                sub_questions=data.get("sub_questions", [question]),
            )
        except json.JSONDecodeError:
            logger.warning("Query rewrite returned invalid JSON (first 200 chars): %s", result[:200])
            return RewriteResult(
                rewritten_query=question,
                sub_questions=[question],
            )


query_rewrite_service = QueryRewriteService()
