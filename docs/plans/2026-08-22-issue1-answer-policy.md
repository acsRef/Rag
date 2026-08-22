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

## 4. Identified gaps（audit 后填）

_执行 audit 后写入。初步预计 4-8 项。_

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

| 测试 | 覆盖模块 | 类型 | 数量预估 |
| --- | --- | --- | --- |
| premise_correction 各 5 类测试 | Premise | 单元 | 5 |
| refusal 5 类场景测试 | Refusal | 单元 | 5 |
| citation 对齐测试 | Citation | 单元 | 3 |
| grounding 通用补充边界测试 | Grounding | 单元 | 3 |
| output format 规则测试 | Output | 单元 | 3 |
| prompt 模块加载/组合测试 | Modularization | 单元 | 2 |
| 集成：pipeline.execute() 在新 prompt 下守住基线 | All | 集成 | 1 |

**总计预估：22 个新测试**。

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
