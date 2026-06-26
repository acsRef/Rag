"""RAG prompt templates with CoT reasoning and token-budget aware formatting."""

from app.models.schemas import RetrievedChunk
from app.config import settings
from app.core.pii_scanner import mask_text as _mask_text


# ------------------------------------------------------------------
# System Prompt — 现在的版本覆盖了角色定义 + 边界情况 + CoT 策略
# ------------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是一个专业的 RAG 知识库助手，使用检索增强生成（Retrieval-Augmented Generation）回答用户问题。\n"
    "\n"
    "## 核心规则\n"
    "1. 回答必须基于提供的检索内容（[Source N] 标记的文本块），不得凭空编造信息。\n"
    "2. 如果检索内容不足以回答当前问题，明确说「我没有找到相关信息」，不要猜测或填补。\n"
    "3. 当检索内容与问题部分相关但不充分时，可以补充通用知识，但必须在回答中区分「文档中的信息」和「我的补充」。\n"
    "4. 回答中引用具体来源时，用 [1] [2] 等标记对应 Source 编号。\n"
    "5. 多轮对话中，注意理解用户使用「它」「这个」「上面说的」等代词——这些通常在对话摘要或上文中有明确的指代对象。\n"
    "\n"
    "## 问题复杂度判断\n"
    "- 简单问题：定义解释、事实查证、单步骤操作。直接回答，无需展开。\n"
    "- 复杂问题：涉及多步推理、跨文档对比、因果关系分析、「为什么」「如何」「有什么区别」等。\n"
    "\n"
    "## 复杂问题的逐步推理（Chain of Thought）\n"
    "遇到复杂问题时，先用 <think>...</think> 标签输出推理过程，再用 <answer>...</answer> 标签给出最终回答。\n"
    "\n"
    "输出格式示例：\n"
    "<think>\n"
    "1. 用户想了解 JWT 密钥的配置方法。\n"
    "2. Source 1 提到了密钥生成命令, Source 3 提到了环境变量配置。\n"
    "3. 综合两个来源, 配置步骤为: 生成密钥 → 放入 .env → 重启服务。\n"
    "</think>\n"
    "<answer>\n"
    "根据文档, JWT 密钥配置需要以下步骤: ...\n"
    "</answer>\n"
    "\n"
    "## 输出规则\n"
    "- 复杂问题：必须使用 <think> 和 <answer> 标签，思考过程和最终回答严格分离。\n"
    "- 简单问题：可以省略 <think>，直接输出 <answer> 或无标签的纯文本回答。\n"
    "- <think> 内部可以自由换行、使用 markdown 列表或代码块。\n"
    "- <answer> 内部应为最终用户可读的 markdown 格式正文。\n"
    "\n"
    "## 边界情况处理\n"
    "- 用户打招呼（「你好」「在吗」）：友好回应，简介自身功能。\n"
    "- 问题模糊不清：请用户澄清或补充更多细节。\n"
    "- 问题完全与知识库无关：说明自己是文档知识助手，建议用户补充相关文档或转向通用 AI 助手。\n"
    "- 用户追问上一个回答的某个细节：优先从对话历史中找到上下文，再查检索内容。\n"
    "- 检索内容之间相互矛盾：指出矛盾点，列出各方来源，不做强硬选择。"
)


# ------------------------------------------------------------------
# KB Answer Template — 有检索内容时使用
# ------------------------------------------------------------------

KB_ANSWER_TEMPLATE = (
    "## 检索内容\n"
    "{context}\n"
    "\n"
    "{history}\n"
    "\n"
    "## 当前问题\n"
    "{query}\n"
    "\n"
    "请基于以上检索内容回答。如为复杂问题，先梳理检索内容中的相关信息再作答。"
)


# ------------------------------------------------------------------
# System-only Template — 无检索内容时使用（fallback 到 LLM 自身知识）
# ------------------------------------------------------------------

SYSTEM_ANSWER_TEMPLATE = (
    "{history}\n"
    "\n"
    "## 当前问题\n"
    "{query}\n"
    "\n"
    "注意：当前没有检索到相关文档内容。请基于自身知识回答。如不确定，如实说明。"
)


class RAGPromptBuilder:
    """Builds the user-prompt from retrieved chunks, history, and summary.

    Only the user-prompt varies; the system-prompt is a static string above.
    Token budget enforcement: if assembled prompt exceeds prompt_max_tokens,
    chunks are truncated first (fewer chunks), then history is cut.
    """

    def build_messages(
        self,
        query: str,
        history: list[dict],
        summary: str,
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[dict]:
        if not retrieved_chunks:
            return self._build_system_only(query, history, summary)
        return self._build_with_chunks(query, history, summary, retrieved_chunks)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build_with_chunks(
        self,
        query: str,
        history: list[dict],
        summary: str,
        chunks: list[RetrievedChunk],
    ) -> list[dict]:
        # Enforce total token budget: slice chunks down if needed
        budget = settings.prompt_max_tokens
        system_tokens = _est(SYSTEM_PROMPT)
        query_tokens = _est(query)
        available = budget - system_tokens - query_tokens

        # Reserve space for history block
        history_str = self._format_history(history, summary)
        history_tokens = _est(history_str)
        available -= history_tokens

        # Trim chunks to fit remaining budget
        trimmed_chunks = self._trim_chunks(chunks, available)

        context_str = self._format_chunks(trimmed_chunks)

        user_prompt = KB_ANSWER_TEMPLATE.format(
            context=context_str,
            history=history_str,
            query=query,
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _build_system_only(
        self,
        query: str,
        history: list[dict],
        summary: str,
    ) -> list[dict]:
        history_str = self._format_history(history, summary)
        user_prompt = SYSTEM_ANSWER_TEMPLATE.format(
            history=history_str,
            query=query,
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _format_chunks(self, chunks: list[RetrievedChunk]) -> str:
        """Render chunks as labelled source blocks."""
        parts = []
        for i, chunk in enumerate(chunks):
            text = chunk.text
            if settings.pii_enabled:
                text = _mask_text(text)
            label = f"[Source {i + 1}]"
            if chunk.title:
                label += f" ({chunk.title})"
            if chunk.section_path:
                label += f" [{chunk.section_path}]"
            parts.append(f"{label}\n{text}")
        return "\n\n".join(parts)

    def _format_history(self, history: list[dict], summary: str = "") -> str:
        """Build the history block: summary (压缩要点) + recent turns."""
        lines = []
        if summary:
            lines.append("## 对话历史摘要（上一阶段讨论的压缩要点）")
            lines.append(summary.strip())
            lines.append("")

        lines.append("## 近期对话原文")
        for m in history:
            role_label = "用户" if m["role"] == "user" else "助手"
            lines.append(f"**{role_label}**: {m['content']}")
        return "\n".join(lines)

    def _trim_chunks(self, chunks: list[RetrievedChunk], token_budget: int) -> list[RetrievedChunk]:
        """Drop chunks from the end until the token estimate fits the budget.

        Each chunk contributes roughly `len(text) / 1.5` + overhead for the
        [Source N] label (~20 tokens).  At least 1 chunk is always kept.
        """
        budget = max(token_budget, 1)
        kept = list(chunks)
        while len(kept) > 1:
            total = sum(_est(c.text) + 20 for c in kept)
            if total <= budget:
                break
            kept = kept[:-1]  # drop last (lowest-rank) chunk
        return kept


def _est(text: str) -> int:
    """Fast token estimate: len / 1.5 (mixed Chinese/English)."""
    if not text:
        return 0
    return max(1, int(len(text) / 1.5))


prompt_builder = RAGPromptBuilder()
