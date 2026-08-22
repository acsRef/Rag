# Issue #1 — Answer Policy 工程化（Audit-First）(2026-08-22)

> **本 plan 只做 audit，不写 prompt 代码。** Issue #1 ≠ 写 4 个新能力，而是把已经存在的回答策略从 mega prompt 工程化。
>
> 上游：[Issue #1 GitHub Issue](https://github.com/acsRef/Rag/issues/1)
> 关联决策：[docs/plans/2026-08-22-phase2-b-recon.md](2026-08-22-phase2-b-recon.md) §9 method note + Phase 1 [docs/plans/2026-08-21-cleanup-phase1.md](2026-08-21-cleanup-phase1.md) §0 总原则

## 1. Scope

对 RAG answer policy 层做 audit → gap fill → 模块化 → 测试。本次 plan **只产出 audit 表 + 模块拆分设计 + 测试清单**，**不修改 `SYSTEM_PROMPT` 文本内容**。

下一份执行 plan（不在本 plan 范围）才动 prompt 代码。

## 2. Current-state audit

### 2.1 三问 audit 框架（每次审一个模块都过这三条）

1. **当前实现** — prompt.py 哪段在做什么？
2. **冲突/重复/执行风险** — 这条规则是否与别的规则冲突、重复，或对模型执行有不可控风险？
3. **缺什么** — Issue #1 acceptance criteria 中是否真正缺测试或缺实现？

### 2.2 7 模块审计表

| 模块 | 当前状态 | 问题 | 是否修改 | 是否需要测试 |
| --- | --- | --- | --- | --- |
| **Grounding**（基于检索内容回答） | ✅ prompt.py:21 `【CRITICAL】回答必须基于检索内容，不得凭空编造` + line 45 信息充分度决策 | 是否与"通用知识补充"（line 46-47）冲突——当检索内容不相关时是否允许 LLM 自补？边界不清 | 可能（明确"完全无检索时不允许通用补充"或允许） | ✅ |
| **Premise correction**（错误前提纠偏） | ✅ prompt.py:26-32 强制纠偏 + 三个示例 + 反例 | (a) 输出位置未规定——纠偏应放在回答开头，但 prompt 未指明；(b) 措辞有"不许顺着用户前提作答"——是否过强，模型可能拒绝所有"假设性"问题？ | 可能（指定输出位置 + 措辞降级） | ✅ |
| **Refusal**（拒答边界） | ✅ prompt.py:34-42 强制拒答 + 5 类场景（市值/单一国家/未来/别家公司/未披露） | (a) 5 类边界硬编码——新业务场景需改代码；(b) 与 Evidence Gate 的语义重叠——Evidence Gate 也是基于 evidence 不足拒答，prompt 端已要求"检索内容不足则拒"；两层重复 | 需要评估（保留 + 文档化"与 Gate 的边界"） | ✅ |
| **Citation**（引用一致） | ✅ prompt.py:22 `引用来源时用 [1][2] 格式对应 Source 编号` + line 105 检查项 | citation-source 对齐**没有机制保证**——prompt 要求"引用对应 Source"，但模型可能编造引用号。当前 retrieval 已给 Source 编号，模型应严格对应；缺测试 | 需要确认（验证生成引用号 ∈ 检索 Source 编号集合） | ✅ |
| **Output format**（回答质量） | ✅ prompt.py:96-114 多条规则：表格优先、简洁、空行、数字原文保留等 | 规则偏多（共 6+5+8 条），单轮 prompt token 占显著；可能影响模型遵守度 | 可能（合并/精简规则 + 优先级标注） | ✅ |
| **Reasoning protocol**（`<think>`） | ⚠️ prompt.py:50-71 "复杂问题必须用 <think> 标签分离思考" + `complexity` 概念（已 A 类决策 DELETE） | **contract risk**——(a) `complexity` 字段已 A 类决策删除，但 SYSTEM_PROMPT 中仍有"复杂问题 → ``"的条件分支 → **split-state**；(b) `` 与 TagStreamParser / SSE / DB `thinking_content` 跨层耦合 | **重点**（与 B 类 `` 协议解耦协调） | ✅ |
| **Prompt modularization** | ❌ 全部塞在 `app/core/prompt.py` 单文件 115 行 `SYSTEM_PROMPT` 字符串 | 测试时无法只 mock 一个 policy；维护时改一个 policy 风险整个 prompt；Issue #1 body 明确要求"Separate answer policy from general system instructions" | **需要** | ✅（模块加载、import 路径、动态切换） |

> Audit 表中的"当前状态"列基于已有 prompt.py 115 行 SYSTEM_PROMPT 全文阅读。执行时需复核。
> "问题"列为初步判断；执行 audit 时可能发现新问题或修正。

## 3. Acceptance criteria mapping（Issue #1 GitHub body）

| Issue #1 acceptance criteria | 当前实现 | 当前测试 |
| --- | --- | --- |
| ✅ Incorrect user assumptions are corrected before answering | prompt.py:26-32 已实现 | **缺**——无专门的 premise correction 测试 |
| ✅ Unsupported enterprise facts are refused instead of inferred | prompt.py:34-42 已实现 | **缺**——无专门的 refusal 测试（5 类场景无单元测试覆盖） |
| ✅ Citations consistently map to retrieved sources | prompt.py:22 + retrieval 已给 Source 编号 | **缺**——无对齐验证测试 |
| ✅ Existing RAG evaluation baseline is not degraded | 当前 baseline `459 passed / 6 failed / 13 skipped`（Phase 1） | 守住基线即满足 |
| ✅ Added tests cover major answer policy behaviors | — | **缺**（这是 audit 的主要交付物之一） |

## 4. Identified gaps（audit 填入：基于 prompt.py 115 行 SYSTEM_PROMPT 全文阅读 + tests/unit/test_prompt.py）

## 4. Identified gaps（audit 填入：基于 prompt.py 115 行 SYSTEM_PROMPT 全文阅读 + tests/unit/test_prompt.py）

### 4.1 Grounding 模块

**当前实现**（[app/core/prompt.py:21](app/core/prompt.py#L21) + line 43-48）：
- L21: 【CRITICAL】回答必须基于检索内容
- L45: "检索内容部分相关 → 用文档信息 + **补充通用知识**，但明确区分「文档中的信息」和「我的补充」"
- L46: "检索内容不相关或无检索 → 说「没有找到相关信息」，不要强行关联"

**冲突检测**：
- **L45 与 Issue #1 Refusal 目标冲突**——Issue #1 要求 "Do not estimate, infer, or replace missing information"；prompt.py 允许"补充通用知识"，两规则对模型行为指令相反
- **L45 与 L46 内部冲突**——"部分相关可补充"与"不相关不可补充"的边界"部分相关"由模型自判，主观
- L45 "明确区分"无机制保证——模型可宣称"我补充的"但实际编造

**修复方向**：
- 删除或收紧 L45 "补充通用知识"路径（与 Issue #1 Refusal 对齐）
- 或显式声明 L45 仅适用于"conversational follow-up / 礼貌确认"场景，不适用于"factual answer"

### 4.2 Premise correction 模块

**当前实现**（line 25-31）：
- L27: "【不许顺着用户的前提作答】"
- L28: "【在回答开头第一句就明确否定前提】"
- L29: "若检索内容不足以下定论，如实说「检索内容无法证实这个前提」"
- L31: 反例

**冲突检测**：
- **"不许顺着用户的前提" 措辞过强**——对"假设性问题"（如"如果 X 增长 50%，会怎样？"）模型可能误触发纠偏
- **"回答开头第一句" 无机制保证位置**——模型可能将纠偏放在第二、三句；测试无法验证
- L28 示例 "这个说法不成立，实际是……" 与 "并非连续三年加大"——两个示例给了两种措辞，模型行为不可预测

**修复方向**：
- "开头"改为强制模板："先纠正（1 句），再基于证据回答"，结构化而非"自然语言"
- "不许顺着用户前提"降级为"应核实前提"，避免假设性问题误触发

### 4.3 Refusal 模块

**当前实现**（line 33-41）：
- 5 类硬编码场景（市值/单一国家/未来/别家公司/未披露）
- L41 "直接说明「年报未披露此数据」"

**冲突检测**：
- **与 Evidence Gate 语义重叠**——Evidence Gate 在 evidence 不足时也拒答，prompt 端 L46 "检索内容不相关或无检索" 也是拒答。三层拒答逻辑（Prompt + Gate + System_Only Template）边界模糊
- **"年报未披露"措辞特化**——L41 用"年报未披露"是 Sany 年报场景，Issue #1 适用于通用 enterprise QA（合同/手册/其他）
- **5 类硬编码扩展性差**——新场景需改 prompt.py 文件，Issue #1 body 要求"extension interface"

**修复方向**：
- 接口化：定义 `RefusalRule` 类，支持动态注册新规则
- 措辞通用化："年报未披露" → "知识库未披露"或"原始材料未提及"
- 文档化与 Evidence Gate 的分工：prompt 端处理"业务定义拒答场景"，Gate 处理"evidence 不足拒答"

### 4.4 Citation 模块

**当前实现**（L22 + L106）：
- L22: 【CRITICAL】引用来源时用 `[1][2]` 格式对应 Source 编号
- L106: 检查项"引用是否都对应到具体的 Source 编号？"

**冲突检测**：
- **无机制保证**——prompt 要求模型"对应"但无验证；模型可编造 `[5]` 即使检索只给了 1-4 个 chunk
- **检查项依赖模型自省**——L106 是 self-check，模型可撒谎

**修复方向**：
- 后处理：解析 generation 中的 `[N]` 引用号，验证 `N ∈ 检索 chunk indices`
- 若引用超界 → 自动校正或标记"未验证引用"
- 这是 Issue #1 acceptance criteria "Citations consistently map to retrieved sources" 的真正实现路径

### 4.5 Output format 模块

**当前实现**（L96-101, L106-114）：
- 6 条回答质量规则 + 8 条检查清单 = 14 条
- 含表格使用、空行、列表、原文保留等

**冲突检测**：
- **L96 vs L100 冲突**——"表格优先用自然语言概括，不要直接复述原始表格" vs "涉及多个条目对比（3 项以上）时可以用表格"。决策维度模糊（"优先"？ "3 项以上"？）
- **L98 "避免多余空行"不可执行**——模型对 whitespace 控制力有限
- **规则过载**——14 条规则 + 其他章节 → 模型遵守度下降（指令稀释效应）

**修复方向**：
- 合并/精简到 6-8 条核心规则
- 表格决策明确："1 条 → 自然语言；2 条 → 自然语言；≥3 条且对比场景 → 表格"
- 删除不可执行规则（空行等）

### 4.6 Reasoning protocol（`<think>`）模块 — **CONTRACT RISK**

**当前实现**（L50-71）：
- L52-57: "复杂度判断"——4 类复杂问题特征
- L59-70: "复杂问题必须用 `<think>` 标签分离思考"
- L110-112: 检查项引用"复杂问题"和"<think>"
- L163: `RAGPromptBuilder.build_messages(complexity="complex")` 死参数

**冲突检测（高严重）**：
- **`complexity` 字段已 Phase 2 A 类决策 DELETE**（[phase2-contract-decisions.md](2026-08-22-phase2-contract-decisions.md)），但 SYSTEM_PROMPT L52-57 仍以"复杂问题"作 `<think>` 触发条件 → **split-state**
- L53 "拿不准时默认走复杂路径"——模型自评 complexity，主观
- L163 dead param 注释 "保留参数兼容性"——A 类决策后已无兼容需求，应清理
- `<think>` 协议跨 5 层：Prompt / TagStreamParser / SSE / frontend / DB `thinking_content`——是 Phase 2 B 类 C 范畴
- L74-86 例子直接给 `<think>` 块格式——若 B 类决定改协议，这里要同步改

**修复方向**（不在本 plan 范围，与 B 类 `<think>` 协议解耦协调）：
- 本 plan 不改 `<think>` 协议本身
- **最小修订**：把 L52-70 改为无条件规则（"所有回答统一 `<think>` 协议"），消除 complexity 触发条件
- 这与 [phase2-b-recon.md §4](2026-08-22-phase2-b-recon.md) §4 normal 化方向一致
- `complexity` 相关字眼从 prompt 中清掉

### 4.7 Prompt modularization 模块

**当前实现**：
- 整个 SYSTEM_PROMPT 是单字符串（L13-115）
- prompt.py 同文件混含：SYSTEM_PROMPT / KB_ANSWER_TEMPLATE / SYSTEM_ANSWER_TEMPLATE / RAGPromptBuilder class
- 测试覆盖：`_est`、`_trim_history`、`_trim_chunks`——**未覆盖任何 policy 文本**

**冲突检测**：
- 单字符串无法单元测试——policy 文本改动无回归保护
- 改动风险：调整一处 policy 需重新跑全套 eval
- Issue #1 body 明确要求"Separate answer policy from general system instructions"

**修复方向**：
- 拆 `app/core/prompts/{base, grounding, premise, refusal, citation, output, reasoning}.py`
- 每模块导出 `get_section() -> str`
- `RAGPromptBuilder` 调用各模块 `get_section()` 拼接（用 `"\n\n".join()` 而非手写拼接）
- 每模块独立单测

### 4.8 Gap 汇总

| Gap ID | 模块 | 描述 | 优先级 |
| --- | --- | --- | --- |
| GAP-01 | Grounding | L45 "补充通用知识" 与 Refusal 冲突；需收紧或删除 | P0 |
| GAP-02 | Premise | "不许顺着用户前提" 过强；"开头第一句" 无位置保证 | P1 |
| GAP-03 | Refusal | 与 Evidence Gate 语义重叠；5 类硬编码；措辞"年报"特化 | P0 |
| GAP-04 | Citation | 无机制保证 `[N]` ∈ 检索 chunk indices；模型可编造 | P0（Issue #1 acceptance 明确要求） |
| GAP-05 | Output | 规则过载（14 条）；L96 vs L100 冲突；空行规则不可执行 | P2 |
| GAP-06 | Reasoning | complexity split-state（字段已删，prompt 仍引用）；L163 死参数 | P0（与 B 类 `<think>` 解耦协调；最小修订） |
| GAP-07 | Modularization | 全部塞在单字符串；policy 无单测 | P0（Issue #1 body 明确要求） |
| GAP-08 | Refusal + Gate | 与 Gate 分工未文档化 | P1 |
| GAP-09 | Test coverage | tests/unit/test_prompt.py 只覆盖 `_est` / trim 逻辑，无 policy 测试 | P0 |
| GAP-10 | Complexity dead param | `RAGPromptBuilder.build_messages(complexity=...)` L163 死参数未清 | P2（与 GAP-06 一起处理） |

> ⚠️ GAP-06 与 Phase 2 B 类 `<think>` 协议解耦决策有交叉。Issue #1 范围内**只做最小修订**（无条件化规则 + 清死参数），不重设计 `<think>` 协议本身。

## 5. Prompt module design（仅设计，audit 阶段不动代码）

目标结构（proposed）：

```
app/core/prompts/
├── __init__.py
├── base.py            # 基础模板（SYSTEM 角色、引用格式、输出格式）
├── grounding.py       # 基于检索内容回答 + 通用补充边界
├── premise.py         # 错误前提纠偏（输出位置 + 措辞）
├── refusal.py         # 5 类场景拒答（可扩展接口）
├── citation.py        # 引用一致性规则
├── output.py          # 输出质量（表格/列表/数字原文）
└── reasoning.py       # `` 协议（与 B 类 `` 解耦协调）
```

每个模块导出 `def get_section() -> str`，由 `RAGPromptBuilder` 组合。**audit 阶段只画框图，不写代码。**

## 6. Test plan（audit 阶段只列清单）

> §6 内容基于 §4 gap 汇总。修复实施阶段（本 plan 范围外）才动代码并写测试。

| Gap | 测试 | 覆盖模块 | 类型 |
| --- | --- | --- | --- |
| GAP-01 | grounding_no_external_knowledge_supplement | Grounding | 单元 |
| GAP-01 | grounding_partial_coverage_handling | Grounding | 单元 |
| GAP-02 | premise_correction_at_first_sentence | Premise | 单元 |
| GAP-02 | premise_hypothetical_question_not_overcorrected | Premise | 单元 |
| GAP-02 | premise_insufficient_evidence_no_guess | Premise | 单元 |
| GAP-03 | refusal_market_cap_refused | Refusal | 单元 |
| GAP-03 | refusal_country_revenue_refused | Refusal | 单元 |
| GAP-03 | refusal_future_target_refused | Refusal | 单元 |
| GAP-03 | refusal_other_company_refused | Refusal | 单元 |
| GAP-03 | refusal_undisclosed_field_refused | Refusal | 单元 |
| GAP-03 | refusal_extension_interface_register | Refusal | 单元 |
| GAP-04 | citation_numbers_match_retrieval_indices | Citation | 单元 |
| GAP-04 | citation_no_fabricated_references | Citation | 单元 |
| GAP-04 | citation_invalid_ref_auto_correct_or_mark | Citation | 单元 |
| GAP-05 | output_table_decision_three_items | Output | 单元 |
| GAP-05 | output_no_redundant_blank_lines | Output | 单元 |
| GAP-06 | reasoning_no_complexity_field_referenced | Reasoning | 单元 |
| GAP-06 | reasoning_always_emitted_unconditional | Reasoning | 单元 |
| GAP-07 | prompt_modules_load_independently | Modularization | 单元 |
| GAP-07 | prompt_compose_full_assembly_no_duplication | Modularization | 单元 |
| GAP-09 | test_critical_policies_have_explicit_test | All | 元测试 |
| — | 集成：pipeline.execute() 在新 prompt 下守住基线（459/6/13） | All | 集成 |

**总计预估：22 个新测试**（与 §5 设计时的预估一致）。

## 7. Baseline / validation protocol

执行完成后：

1. **pytest 全量**：`459 passed / 6 failed / 13 skipped`（基线守口；新增 ~22 测试为 +N passed）
2. **ruff check + format**：全绿
3. **V3 小样本重测**（10 题与 Stage 2 同集合）：仅 `off` 配置
   - 对比旧 V3 baseline（4/10 正确）vs 新 prompt
   - **不重做 65 题**——Issue #1 的目的是确认 prompt 改动质量，10 题足够
4. **新 baseline 锁定**：V3 10 题新结果入 docs/plans/

> ⚠️ Issue #1 完成**不能立即做 Gate 实验**。新 baseline 锁定后再单独做 ConflictDetector 检查 + Gate 重测（见 §9）。

## 8. Non-goals

**本 plan 不动**：

- ✗ 不改 retrieval（hybrid_search / cross_doc / 等）
- ✗ 不改 embedding
- ✗ 不改 reranker
- ✗ 不改 Evidence Gate（Evidence Gate 接线 / 阈值 / 开关都不动）
- ✗ 不改 model routing（`_is_complex_query` 不动；intent_model / chat_model / rewrite_model 都不动）
- ✗ 不改变当前 baseline 数据集（65 题 + 测试用例）
- ✗ 不重跑评测 ablation（仅 V3 10 题小样本）
- ✗ 不动 Issue #2（table-aware chunking/retrieval）

## 9. Execution sequence

```
current (master @ 8d08de4)
    ↓
Phase A — Audit（本 plan）
    1. 读 prompt.py 全文 + AGENTS.md §3 §8 + test_prompt.py + test_query_parser.py
    2. 填 §2.2 audit 表 + §4 gap 清单
    3. 产出 audit 报告（独立 doc 或本 plan §4）
    4. ★ STOP — 等用户确认 audit 结论后再写代码
        ↓
Phase B — Gap fill + 模块化（独立 plan，不在本 plan 范围）
    ↓
Phase C — 测试（独立 plan）
    ↓
Phase D — V3 10 题小样本重测 → ★ NEW BASELINE
        ↓
Phase E — ConflictDetector 单查 → 修（独立 plan）
        ↓
Phase F — Evidence Gate 重测（基于新 baseline）
        ↓
Phase G — Issue #2 排队
```

**关键纪律**：每个 Phase 产出独立 commit + plan doc；前一个 Phase 不通过不进下一个。

## 10. Validation Strategy

- 每个 commit 后跑 `pytest tests/unit -q`，守住基线
- Phase B/C 完成后 ruff 全绿
- Phase D 完成后 V3 10 题结果与旧 baseline 对比成 doc

## 11. 边界提醒

| 项 | 边界 |
| --- | --- |
| `complexity` 字段 | 已 A 类决策 DELETE；本 plan §2.2 Reasoning protocol 行已标记 `complexity` split-state 问题，**与 B 类 `` 协议解耦协调处理，不在本 plan 单独解决** |
| Evidence Gate | §8 明确 non-goal；Gate 实验在 Phase F 重做 |
| Issue #2 | §8 明确 non-goal；本 plan 完成后 Issue #2 才启动 |

---

## 附录 A：Issue #1 GitHub Issue 引用

> Title: Improve RAG answer policy for grounding, refusal and factual correction
> URL: https://github.com/acsRef/Rag/issues/1
> Created: 2026-08-22
> Non-goals 明确包括：retrieval / embedding / reranking / indexing / Evidence Gate

## 附录 B：本 plan 与 recon 方法论的一致性

| recon §9 原则 | 本 plan 体现 |
| --- | --- |
| 不要把"字段存在 / Prompt 提到 / runtime 写入 / runtime 消费 / 诊断输出"混为一谈 | §2.2 Reasoning protocol 行专门列出 `complexity` 字段已删但 prompt 文本仍引用 |
| field vs concept 严格区分 | §2.2 Refusal 行标注"与 Evidence Gate 语义重叠"，但不混用 |
| 伴随改动必须 explicit 列出 | §8 Non-goals 列出 8 项 |
| DELETE 是合理结局 | audit 阶段不预设"prompt 必须改"，可能结论是"现状已足够，加测试即可" |
