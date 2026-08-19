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
from app.config import settings
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

# 特殊场景处理

## 年份/时间范围拆分
当问题涉及多个年份或时间范围时（如"2023-2025年"、"近三年"、"分别"），拆分为每个年份/时间段的独立子问题：
- "2023-2025年营收分别是多少" → 拆为 "2023年营收"、"2024年营收"、"2025年营收"
- "近三年海外收入" → 拆为每年的海外收入子问题
- 如果无法确定具体年份，保留原始查询但确保子问题包含时间限定

## 前提验证
当问题包含可能不正确的前提时（如"为什么X增长了"、"X增加了多少"），增加一个验证性子问题：
- "为什么研发投入连续三年加大" → 子问题增加 "研发投入近三年的实际变化趋势"
- "混凝土机械收入同比增长了多少" → 子问题增加 "混凝土机械收入的实际同比变化"
- 验证性子问题用于检索实际数据，帮助模型判断前提是否成立

## 跨文档/跨实体对比
当问题涉及多个实体或文档的对比时，拆分为各实体的独立查询：
- "A 和 B 的区别" → 拆为 "A 的特点"、"B 的特点"、"A 和 B 的区别"

# 边界处理

- 没有问题 → 直接原样返回
- 纯社交用语（你好、在吗、谢谢）→ 原样返回，sub_questions 只含原问题
- 问题很短但信息完整（如"什么是 RAG"）→ 保持原样，不需要展开
- 问题中的技术术语、版本号、专有名词必须原样保留，不得概括

# 示例

用户问题："如何实现它？"
对话摘要："用户询问了 Transformer 注意力机制的原理，已解释 QKV 计算方式"
输出：{{"rewritten_query": "如何用 PyTorch 实现 Transformer 注意力机制的 QKV 计算", "sub_questions": ["如何用 PyTorch 实现 Transformer 注意力机制的 QKV 计算"]}}

用户问题："2023-2025年三一重工营业收入分别是多少？"
对话摘要：""
输出：{{"rewritten_query": "三一重工2023-2025年营业收入", "sub_questions": ["三一重工2023年营业收入", "三一重工2024年营业收入", "三一重工2025年营业收入"]}}

用户问题："为什么三一重工连续三年加大研发投入金额？"
对话摘要：""
输出：{{"rewritten_query": "三一重工研发投入变化趋势", "sub_questions": ["三一重工研发投入近三年的实际变化趋势", "三一重工各年研发投入金额及占营收比例"]}}

用户问题："2024年三一重工混凝土机械收入同比增长了多少？"
对话摘要：""
输出：{{"rewritten_query": "2024年三一重工混凝土机械收入同比变化", "sub_questions": ["2024年三一重工混凝土机械销售收入", "2023年三一重工混凝土机械销售收入", "混凝土机械收入的实际同比变化"]}}

用户问题："什么是 RAG？"
对话摘要：""
输出：{{"rewritten_query": "什么是 RAG", "sub_questions": ["什么是 RAG"]}}

对话摘要：
{summary}

最近对话：
{history}

用户问题：{question}

# 输出格式
{{"rewritten_query": "改写后的主查询", "sub_questions": ["子问题1", "子问题2", ...], "sub_dependencies": [[], [0], [0,1]], "complexity": "complex"}}

`sub_dependencies` 标注依赖关系（0-based 索引）：
- `[]` 无依赖，独立检索
- `[0]` 依赖第 1 个子问题的结果
- `[0,1]` 依赖前两个

如果只有一个问题，sub_questions 只含改写后的查询，sub_dependencies 为 `[[]]`。

## 子问题依赖示例（覆盖常见模式）

【示例 A · 独立拆分】无依赖，平行检索
问题："近三年营收分别是多少？"
输出：{{"rewritten_query": "近三年营收对比", "sub_questions": ["2023年营收", "2024年营收", "2025年营收"], "sub_dependencies": [[], [], []]}}

【示例 B · 链式推理】后续子问题依赖前面的检索结果
问题："为什么2025年净利润大增？"
输出：{{"rewritten_query": "2025年净利润大增原因分析", "sub_questions": ["2025年净利润数据", "2025年营收和毛利变化", "2025年成本费用变化"], "sub_dependencies": [[], [0], [0,1]]}}

【示例 C · 前提验证】验证子问题先于主推理
问题："为什么研发投入连续三年加大？"
输出：{{"rewritten_query": "研发投入近三年实际变化", "sub_questions": ["研发投入近三年实际数值", "研发投入与营收比例变化"], "sub_dependencies": [[], [0]]}}

【示例 D · 综合判断】先检索证据再综合判断
问题："判断盈利改善是否靠国内市场爆发"
输出：{{"rewritten_query": "盈利改善驱动因素分析", "sub_questions": ["近三年营收增速", "国际国内占比变化", "国际毛利率趋势"], "sub_dependencies": [[], [0], [0,1]], "complexity": "complex"}}

## 难度分类（控制 CoT 触发）

`complexity` 控制下游是否触发 Chain-of-Thought：
- `"simple"`: 直接事实查询，单点信息——不要触发 CoT，直接给答案
  - 例：「2023 年营收是多少？」「公司有哪些子公司？」「专利申请多少件」
- `"complex"`: 需要跨文档/多步骤推理/前提验证/趋势判断——触发 CoT
  - 例：「判断盈利改善是否靠国内爆发」「为什么研发投入连续三年加大」「近三年海外收入趋势」

判断要点：
- 只问单个数字/单点信息 → simple
- 涉及「为什么」「判断」「是否成立」「趋势」「差异」「变化」→ complex
- 需要跨年/跨文档综合对比 → complex
- 包含「请解释」「如何」「原因」 → complex

# 输出前确认
□ 所有代词都已消除？
□ 改写后的查询能独立理解？
□ 没有引入原文不存在的信息？
□ 涉及多年度时是否拆分为各年子问题？
□ 问题前提可能不正确时是否增加了验证性子问题？
□ sub_dependencies 是否正确标注依赖关系？
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
        # 守卫：LLM 可能显式返回空 sub_questions（[] 会让 .get 默认值失效，
        # 下游 pipeline 的 sub_queries[0] 直接 IndexError）；过滤非字符串/空白项，
        # 空列表回退到 rewritten_query，rewritten 缺失再回退原 query。
        rewritten = data.get("rewritten_query") or question
        subs = [s for s in (data.get("sub_questions") or [])
                if isinstance(s, str) and s.strip()]
        # 封顶：LLM 控制的 sub_questions 数量无上限会触发 gather 并发雪崩
        # （rerank 无内置限流）；按 settings 截断到 max_sub_questions。
        if len(subs) > settings.max_sub_questions:
            subs = subs[:settings.max_sub_questions]
        if not subs:
            subs = [rewritten]
        # 解析 sub_dependencies（0-based 索引列表的列表）
        # 容错：长度不对/类型不对/索引越界时降级为全无依赖
        raw_deps = data.get("sub_dependencies") or []
        deps: list[list[int]] = []
        if isinstance(raw_deps, list) and len(raw_deps) == len(subs):
            for i, dep in enumerate(raw_deps):
                if not isinstance(dep, list):
                    deps.append([])
                    continue
                # 只保留合法范围内且 < i 的索引（不能依赖自己或后续）
                valid = [int(d) for d in dep if isinstance(d, int) and 0 <= d < i]
                deps.append(valid)
        else:
            deps = [[] for _ in subs]

        # 解析 complexity（控制 CoT 触发）
        # 失败时默认 "complex"——保守触发 CoT 防止误判
        complexity = data.get("complexity", "complex")
        if complexity not in ("simple", "complex"):
            complexity = "complex"

        return RewriteResult(
            rewritten_query=rewritten,
            sub_questions=subs,
            sub_dependencies=deps,
            complexity=complexity,
        )


query_rewrite_service = QueryRewriteService()
