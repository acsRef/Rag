# 下一步计划 (2026-08-23)

> **方法论纪律**（来自 [memory/eval-methodology-feedback-2026-08-23.md](../../memory/eval-methodology-feedback-2026-08-23.md)）：
> - 复合变更 ≠ 单变量归因
> - judge noise 必须与 detector 同等级关注
> - 不预设准确率目标，用 acceptance criteria
> - Gate 实验必须在 detector + judge 都干净后再做

## 本次完成（Issue #1-A 执行 + Baseline-1 锁定）

### Issue #1-A — complexity split-state + 死参数清理

**5 commits** landed on master（plan §8 写的是 6 个 commit，ruff format 无变更故省略）：

```
8a7bb34 test(rewrite_complexity): update mock JSON to drop complexity field
44ad69d chore(prompt): make <think> unconditional + remove build_messages dead param
ca467a3 chore(pipeline): remove query_complexity variable
659f606 chore(rewrite): remove complexity from R1 prompt + parser + 难度分类 section
86b5daa chore(schemas): remove RewriteResult.complexity field (Phase 2 A-class decision execution)
```

**Plan 偏离**（与 [docs/plans/2026-08-22-issue1-a-contract-cleanup.md](../../docs/plans/2026-08-22-issue1-a-contract-cleanup.md) 比较）：

| 偏离 | 理由 |
|---|---|
| §3.2 prompt section "## 难度分类（控制 CoT 触发）"（lines 168-180）整段删除 | plan §1 "彻底清除 complexity" + D2 无条件规则 |
| §3.3 line 335 orphan comment 删除 | line 336 变量删除后注释成 orphan |
| §3.4.1 示例1 heading + trailing blank line | 章节间距清理 |
| Commit 6 ruff format 跳过 | ruff 无变更，空 commit 不创建 |

### 测试基线守口

| | 修复前 | 修复后 |
|---|---|---|
| 全量 pytest | `468 passed / 6 failed / 13 skipped` | `468 passed / 6 failed / 13 skipped` |
| 6 failed 集合 | 不变 | 不变 |
| ruff check | 全绿 | 全绿 |

### Baseline-1 锁定（V3 10 题小样本）

**Config**: `CHAT_MODEL=deepseek-ai/DeepSeek-V3` + Gate off + Issue #1-A applied + 10 题
**Output**: `eval/ablation/20260822T160235/`

| Metric | Baseline-0 | **Baseline-1** | Δ |
|---|---|---|---|
| accuracy | 40% (4/10) | **60% (6/10)** | **+20pp** |
| refusal_rate | 0% | 0% | — |
| false_refusal_rate | 0% | 0% | — |
| latency_p50 | 8.9s | 9.0s | +0.1s |
| latency_p95 | 12.2s | 12.1s | -0.1s |

**正确归因**（per feedback）：

> **Baseline-1 优于 Baseline-0（+20pp），且无回归；这证明 Issue #1-A 的整体改动（schema 字段 + prompt contract + parser + pipeline + 测试更新）没有破坏生成链路，并且当前 prompt contract 比 Baseline-0 更有效。**
>
> **不能把 +20pp 单独归因到 "complexity 删除"。** 这是 composite change，单变量归因必须做 ablation 验证（后续 P2 阶段再考虑）。

**Per-question Δ**：

| Q | Baseline-0 | Baseline-1 | 备注 |
|---|---|---|---|
| Q01 | ❌ | ❌ | A 类，年报 2023 营收（gold 732.22亿） |
| Q02 | ✅ | ✅ | A 类 |
| Q03 | ❌ | ✅ | A 类，2025 经营现金流净额 |
| Q04 | ❌ | ❌ | A 类 |
| Q05 | ✅ | ✅ | A 类 |
| Q06 | ✅ | ✅ | A 类 |
| Q07 | ❌ | ❌ | A 类 |
| Q08 | ✅ | ✅ | A 类 |
| Q09 | ❌ | ✅ | A 类 |
| Q10 | ❌（**judge false-negative**） | ❌（**judge false-negative**） | A 类，生成答案 0.9834元/股 与 gold 完全一致 |

**已知观测点问题**：

1. **Q10 judge 误判** — 模型答「0.9834 元/股」与 gold 完全一致，judge 判 incorrect。`Observed accuracy ≠ True accuracy`，evaluator 本身需要审计。
2. **ConflictDetector 高噪声** — 9/10 题 `temporal_consistent=False`（来自 Stage 2 ablation）。detector 的 comparison key 需要审查（不同年份被识别成 conflict 的话必须改）。

### Issue #1-A 仍存（出 plan 范围，待后续）

| 项 | 备注 |
|---|---|
| `KB_ANSWER_TEMPLATE` line 115「如为复杂问题」软提示 | Issue #1-E（wording）一并扫 |
| `tests/unit/test_rewrite_complexity.py` 文件名 | Issue #1-D 重命名 |
| ConflictDetector 高 temporal_consistent=False 噪声 | **P0 单独审计**（决定修 vs 删） |
| `<think>` 协议层重构 | B/C 类，独立 plan |

## 下一步优先级（修订后）

### P0 — 并发审计（任何后续实验的前提）

1. **ConflictDetector 逐题审计**
   - 目标：把 9/10 题 `temporal_consistent=False` 区分为：
     - True conflict（同 entity + 同 metric + 同 period + 不同 source + 值不一致）
     - False positive（不同年份 / 不同口径 / 同一指标趋势变化）
     - Ambiguous（人工判断）
   - 关键 question：detector 是否把 "不同年份" 误判为 conflict？comparison key 至少应隐含 (entity, metric, period) 三元组
   - 输出：detector 的 precision/recall 估计 + 修 vs 删决策

2. **Judge noise 审计**
   - 目标：量化 evaluator 的 Observed accuracy 与 True accuracy 的偏差
   - 方法：抽 5-10 题（含已知正确 / 已知错误 / 边界题）手验，对比 judge 判定
   - 关键 question：Q10 类误判的频率？何种 answer 形态容易触发？
   - 输出：judge precision/recall 估计 + 修 vs 换 (deterministic judge / 加人工 spot check)

### P1 — 系统改进（审计完成 + 单变量实验）

3. **Issue #1-B**（modularization + tests）— GAP-07 + GAP-09。Prompt 字符串无单测是当前测试覆盖率盲区。
4. **Issue #1-C**（citation runtime validation）— GAP-04。prompt 是软约束，必须代码层校验 `[1][2]` 引用号是否对应实际 sources。
5. **Issue #1-D**（policy boundary）— GAP-01 + GAP-03 + GAP-08。定义 Evidence Gate vs Refusal Policy 分工。
6. **Gate pilot** — **仅在 P0 两项审计完成后启动**。否则 detector + judge 同时有噪声，Gate 实验数据难以解释。

### P2 — 队列

7. **Issue #1-E**（wording）— GAP-02 + GAP-05；含 `KB_ANSWER_TEMPLATE` line 115 软提示清理 + 14 条 checklist 收口。
8. **B2**（query_type DELETE）— 独立 plan，commit 已决策但未执行。
9. **Issue #2**（table-aware chunking）— 排队等 Issue #1 收口。
10. **全量 65 题 re-test** — 当前 Baseline-1 只是 10 题样本，全量验证待 P0/P1 关键决策后做。

## Acceptance Criteria（替代 preset 目标）

**不**写 "accuracy ≥ 65%" / "≥ 70%" 这类 preset 数字。每次改动必须满足：

| 项 | 门槛 |
|---|---|
| 测试基线 | `468 passed / 6 failed / 13 skipped` 守口（6 failed 集合禁止新增） |
| Eval 改进 | accuracy 提升 ≥ 2pp（vs 上一 baseline）**AND** 无回归 **AND** latency p95 overhead < 20% |
| 单变量 | 每次改动只能动一个变量（只改 schema / 只改 prompt / 只接 gate） |
| 归因 | summary 必须明确指出 single-variable vs composite；composite 不能归因到单一变量 |
| Judgement | 每个 per-question 结论必须有可验证的 judge / 引用 / 手验依据 |

## 当前状态总览

```
Baseline-0
V3: 40% (历史 V3 + 旧 prompt + 旧 contract)
        ↓
Issue #1-A
composite change: schema + prompt + parser + pipeline + test
        ↓
Baseline-1
V3: 60% (历史 V3 + 新 contract)
        ↓
★ CURRENT
        |
        +── P0: ConflictDetector 审计
        |
        +── P0: Judge noise 审计
        |
        +── P1: Issue #1-B
        |
        +── P1: Issue #1-C
        |
        +── P1: Issue #1-D
        |
        +── P1: Gate pilot (审计完成后)
        |
        +── P2: Issue #1-E
        |
        +── P2: B2 query_type
        |
        +── P2: Issue #2
        |
        +── P2: 65 题全量
```

## 不变量（每次修改必守）

- 测试基线 `468 passed / 6 failed / 13 skipped` 必须守口
- 6 failed 预存失败集合禁止新增
- 每次修改独立 baseline（Baseline-0 → Baseline-1 → Baseline-2 → ... → Experiment），不混叠
- Eval 数据集为 10 题子集（首个 10 个 A 类），全量 65 题保留作 P2 阶段验证
- 任何 "X% 提升" 必须区分 single-variable vs composite；composite 不能归因
- 任何 judge 决策必须能追溯到具体 generation_answer / gold 对比；noise 标记必须明显
