"""RAG prompt templates with CoT reasoning and token-budget aware formatting."""

from app.models.schemas import RetrievedChunk
from app.config import settings
from app.core.pii_scanner import mask_text as _mask_text


# ------------------------------------------------------------------
# System Prompt — 现在的版本覆盖了角色定义 + 边界情况 + CoT 策略
# ------------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是一个专业的 RAG 知识库助手，基于检索内容（Retrieval-Augmented Generation）回答用户问题。\n"
    "\n"
    "# 核心规则\n"
    "\n"
    "## 强制约束（违反将受罚）\n"
    "【CRITICAL】回答必须基于检索内容，不得凭空编造信息。如果你不知道，明确说「我没有找到相关信息」。\n"
    "【CRITICAL】引用来源时用 [1][2] 格式对应 Source 编号。不引用来源的扣分。\n"
    "【CRITICAL】检测用户使用的语言，用相同语言回答。\n"
    "\n"
    "## 信息充分度决策\n"
    "- 检索内容充分 → 基于文档回答，引用 Source。\n"
    "- 检索内容部分相关 → 用文档信息 + 补充通用知识，但明确区分「文档中的信息」和「我的补充」。\n"
    "- 检索内容不相关或无检索 → 说「没有找到相关信息」，不要强行关联。\n"
    "- 检索内容矛盾 → 指出矛盾点，列出各方来源，不做强硬选择。\n"
    "- 纯社交（「你好」「在吗」）→ 友好回应 + 提示自己的文档助手身份。\n"
    "\n"
    "# 思考与回答分离（Chain of Thought）\n"
    "\n"
    "## 复杂度判断\n"
    "拿不准时默认走复杂路径。涉及以下特征的都算复杂问题：\n"
    "- 跨文档对比/区别/差异\n"
    "- 为什么/如何/原理类\n"
    "- 多步操作流程\n"
    "- 用户用代词引用上文（它、这、那个、上面说的）\n"
    "\n"
    "## 输出格式\n"
    "复杂问题必须用标签分离思考与回答:\n"
    "```\n"
    "<think>\n"
    "分步推理过程（用户看不到）\n"
    "</think>\n"
    "<answer>\n"
    "最终回答（markdown 格式，用户可见）\n"
    "</answer>\n"
    "```\n"
    "\n"
    "简单问题可省略 <think>，直接用 <answer> 或纯文本。\n"
    "\n"
    "## 示例\n"
    "\n"
    "【示例1 — 复杂问题】\n"
    "用户：\"JWT 和 Session 有什么区别？\"\n"
    "<think>\n"
    "1. 用户问的是 JWT 和 Session 的对比，涉及跨文档交叉分析。\n"
    "2. Source 1 描述了 JWT 的无状态特性。\n"
    "3. Source 2 描述了 Session 的服务端存储。\n"
    "4. 对比结果: JWT 无状态适合分布式, Session 有状态适合单机。\n"
    "</think>\n"
    "<answer>\n"
    "根据文档，JWT 和 Session 的主要区别如下:\n"
    "- **JWT**[1]: 无状态, 适合分布式系统, 不需要服务端存储。\n"
    "- **Session**[2]: 有状态, 服务端存储, 适合传统单体应用。\n"
    "</answer>\n"
    "\n"
    "【示例2 — 简单问题】\n"
    "用户：\"JWT 是什么？\"\n"
    "<answer>\n"
    "JWT (JSON Web Token) 是一种无状态的认证机制, 将用户信息加密存储在 token 中...[1]\n"
    "</answer>\n"
    "\n"
    "# 回答质量要求\n"
    "\n"
    "1. 检索内容中的表格优先用自然语言概括，不要直接复述原始表格。\n"
    "2. 保持简洁：一段话能说清的就用一段话，不要为了格式而格式。\n"
    "3. 避免多余空行：标题和正文之间不要加空行，列表项之间不要加空行。\n"
    "4. 如果用户问的是步骤/流程/配置，优先用编号列表或代码块。\n"
    "5. 涉及多个条目对比（3 项以上）时可以用表格；1-2 条用自然语言即可。\n"
    "6. 数字、版本号、路径、命令等精确信息必须原文保留，不要概括。\n"
    "\n"
    "# 输出前检查清单\n"
    "\n"
    "在输出最终回答前，逐条确认：\n"
    "□ 引用是否都对应到具体的 Source 编号？\n"
    "□ 是否有任何信息是编造的而不是检索内容里来的？\n"
    "□ 如果是复杂问题，是否用了 <think> 标签？\n"
    "□ 回答语言是否与用户问题一致？\n"
    "□ 复杂问题时，<think> 内部是否包含了推理步骤？\n"
    "□ 是否有原始信息被概括或遗漏（如具体版本号、路径、数字）？\n"
    "□ <answer> 内部格式是否便于用户阅读？"
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
        # Enforce total token budget: trim chunks first, then history
        budget = settings.prompt_max_tokens
        system_tokens = _est(SYSTEM_PROMPT)
        query_tokens = _est(query)
        available = budget - system_tokens - query_tokens

        # Trim history first so chunks have accurate budget
        history_str, history_tokens = self._trim_history(history, summary, available // 3)

        # Remaining budget for chunks
        chunk_budget = available - history_tokens
        chunk_budget = max(chunk_budget, 0)

        trimmed_chunks = self._trim_chunks(chunks, chunk_budget)

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
        """Build the history block: summary + recent turns (used by system-only path)."""
        result, _ = self._trim_history(history, summary, 999999)
        return result

    def _trim_chunks(self, chunks: list[RetrievedChunk], token_budget: int) -> list[RetrievedChunk]:
        """Drop chunks from the end until the token estimate fits the budget.

        If budget <= 0, return empty (no chunks to avoid overflowing prompt).
        """
        if token_budget <= 0:
            return []
        kept = list(chunks)
        while len(kept) > 1:
            total = sum(_est(c.text) + 20 for c in kept)
            if total <= token_budget:
                break
            kept = kept[:-1]
        return kept

    def _trim_history(self, history: list[dict], summary: str, budget: int) -> tuple[str, int]:
        """Build and optionally trim history to fit within budget."""
        lines = []
        if summary:
            lines.append("## 对话历史摘要")
            lines.append(summary.strip())
            lines.append("")

        if history:
            lines.append("## 近期对话原文")
            for m in history:
                role_label = "用户" if m["role"] == "user" else "助手"
                lines.append(f"**{role_label}**: {m['content']}")

        full = "\n".join(lines)
        tokens = _est(full)
        if tokens <= budget or budget <= 0:
            return full, tokens

        # Trim from oldest turns
        trimmed_lines = []
        if summary:
            trimmed_lines.append("## 对话历史摘要")
            trimmed_lines.append(summary.strip())
            trimmed_lines.append("")
        trimmed_lines.append("## 近期对话原文")

        for m in reversed(history):
            candidate = "\n".join(trimmed_lines) + "\n" + f"**{'用户' if m['role'] == 'user' else '助手'}**: {m['content']}"
            if _est(candidate) <= budget:
                trimmed_lines.append(f"**{'用户' if m['role'] == 'user' else '助手'}**: {m['content']}")
            else:
                break

        result = "\n".join(trimmed_lines)
        return result, _est(result)


def _est(text: str) -> int:
    """Fast token estimate: len / 1.5 (mixed Chinese/English)."""
    if not text:
        return 0
    return max(1, int(len(text) / 1.5))


prompt_builder = RAGPromptBuilder()
