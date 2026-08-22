# 下一步计划 (2026-08-23) — Phase 4 收口 + Judge calibration 立项

> **方法论纪律**（[memory/eval-methodology-feedback-2026-08-23.md](../../memory/eval-methodology-feedback-2026-08-23.md)）：
> - 复合变更 ≠ 单变量归因
> - judge noise 必须与 detector 同等级关注
> - 不预设准确率目标，用 acceptance criteria
> - **Gate 实验必须在 detector + judge 都干净后再做**（当前 Gate 已 KEEP DISABLED）

## 本次会话完成（截至 2026-08-23）

| 阶段 | 产出 |
|---|---|
| Issue #1-A 执行 | 5 commits + ruff format 跳过 |
| 测试基线守口 | 468 passed / 6 failed / 13 skipped（不变）|
| Baseline-1 锁定 | V3 40-60% on 10q（composite change）|
| P0 双审计 | Judge 10-20% noise + ConflictDetector 9/10 FP |
| Phase 4 (4-A/B/C/D) | ConflictKey 契约 + detector 9→4/10 FP + severity-aware gate + Gate pilot 数据 |
| **Phase 4 结论** | **Evidence Gate KEEP DISABLED / DEFER**（详见 pilot report）|

## 当前状态总览（项目级）

```
Phase 0
baseline
        ↓
Phase 1
engineering cleanup
        ↓
Phase 2 A
contract cleanup
        ↓
Issue #1-A
complexity split-state cleanup
        ↓
Baseline-1
V3 40-60%
        ↓
Phase 4
Evidence contract repair
        ↓
Gate pilot
        ↓
❌ net-negative (0 recovery + 3 false refusal)
        ↓
Evidence Gate = KEEP DISABLED / DEFER
        ↓
★ CURRENT (2026-08-23)
        |
        +── P0: Judge calibration (15 题手验)
        |
        +── P1: Issue #1-B → 1-C → 1-D
        |
        +── P2: B2 / Table-aware / Model routing / MCP / 65q benchmark
```

## 下一步优先级

### P0 — Judge calibration（小规模先做）

> **Why first**：当前 judge 10-20% noise，所有后续优化（prompt / model / reranker / table / MCP）都可能被 evaluator 污染。15 题手验是便宜的保险。

1. **15 题 hand-verified gold subset**
   - 从 65 题抽 15 题（每类 A/B/C/D/E 至少 2 题 + tricky 题）
   - 建 `eval/sany_annual_reports/human_verified_15.json`
   - 字段：question / gold_answer / human_label (correct/partial/wrong/refusal/unsupported) / human_reason
   - 不重写 evaluator — 只测量 current DeepSeek-V3 judge ↔ human agreement

2. **Agreement 计算**
   - 计算 LLM judge ↔ human 的 confusion matrix
   - 计算 Cohen's kappa / F1 / precision / recall
   - 重点关注 partial answer（当前 judge 无 partial 档，全部判 incorrect）

3. **Decision**
   - 如果 15 题里 judge 只错 1-2 个边界问题 → 锁定 evaluator
   - 如果错得多 → 进入 evaluator 改进（deterministic numeric checks + structured LLM judge）

### P1 — Issue #1-B → 1-C → 1-D（顺序执行）

> **Why this is high-value**：Baseline-1 已 +20pp（composite），证明 prompt/answer policy 改动值得继续。Issue #1-B/C/D 在 prompt architecture 层有结构性收益。

4. **Issue #1-B** — modularization + tests（GAP-07 + GAP-09）
   - Prompt 字符串无单测是当前测试覆盖率盲区
   - 拆 `app/core/prompt.py` 为多个独立常量 + 单测

5. **Issue #1-C** — citation runtime validation（GAP-04）
   - Prompt 是软约束，必须代码层校验 `[1][2]` 引用号是否对应实际 sources
   - 比 prompt engineering 更可靠

6. **Issue #1-D** — policy boundary（GAP-01 + GAP-03 + GAP-08）
   - 定义 Evidence Gate vs Refusal Policy 分工
   - 衔接 Phase 4 的 severity-aware gate 设计

### P2 — 队列

7. B2 query_type DELETE
8. Table-aware ingestion（Issue #2）
9. Model routing
10. MCP → ReportAgent
11. 65 题正式 benchmark（freeze evaluator 后）
12. LLM-based ConflictDetector（**Future/Research** — 仅当其他工作完成后评估 ROI）

## Acceptance Criteria（替代 preset 目标）

| 项 | 门槛 |
|---|---|
| 测试基线 | `468 passed / 6 failed / 13 skipped` 守口 |
| Eval 改进 | accuracy 提升 ≥ 2pp AND 无回归 AND latency p95 overhead < 50% |
| 单变量 | 每次改动只能动一个变量 |
| 归因 | summary 必须明确指出 single-variable vs composite |
| Judgement | 每个 per-question 结论必须有可验证的 judge / human-verified 依据 |
| **Judge agreement** | LLM judge ↔ human_verified_15 agreement ≥ 80%（Phase 5 验收）|

## 关键决策固化

- **Issue #1-A 单点 delta**：composite change（不能单独归因）
- **ConflictDetector FP**：9/10 → 4/10（Phase 4-B）
- **Evidence Gate**：**KEEP DISABLED / DEFER**（不是 DELETE，详见 pilot report）
- **LLM-based ConflictDetector**：**Future/Research candidate**（不在当前 roadmap）
- **Composite change ≠ single-variable attribution**：所有 summary 遵守
- **No preset accuracy targets**：使用 acceptance criteria

## 不变量（每次修改必守）

- 测试基线 `468 passed / 6 failed / 13 skipped` 必须守口
- 6 failed 预存失败集合禁止新增
- 每次修改独立 baseline（Baseline-0 → Baseline-1 → Baseline-2 → ... → Experiment），不混叠
- Evidence Gate 保持 KEEP DISABLED（不跑更多 Gate ablation）
- 不引入 LLM-based ConflictDetector（ROI 差）
- Judge 行为在 Judge calibration 完成前不改
- 15 题 human_verified subset 是所有后续实验的 calibration 基础

## 当前可复用的 Phase 4 产出

| 模块 | 价值 |
|---|---|
| `app/core/conflict_key.py` | 5 元组 dataclass — structured findings 基础 |
| `app/core/conflict_key_extractor.py` | regex-based extractor — 升级到 LLM 时只需替换实现 |
| `app/core/evidence.py` (Phase 4-B/C) | severity-aware gate + ConflictDetector — DEFER 状态可恢复 |
| `tests/unit/test_conflict_key.py` | 22 个 dataclass 单测 — 契约保护 |
| `tests/unit/test_conflict_detector.py` | 11 个 detector 单测 — 行为保护 |
| `tests/unit/test_evidence_gate_severity.py` | 11 个 gate severity 单测 — 决策契约保护 |
| `docs/plans/2026-08-23-conflict-key-spec.md` | 契约 spec — 升级参考 |

## 关联

- Phase 4 pilot report: [docs/plans/2026-08-23-phase4-d-gate-pilot-report.md](2026-08-23-phase4-d-gate-pilot-report.md)
- Phase 4 memory: [memory/phase4-evidence-contract-repair.md](../../memory/phase4-evidence-contract-repair.md)
- Method: [memory/eval-methodology-feedback-2026-08-23.md](../../memory/eval-methodology-feedback-2026-08-23.md)
- Issue #1-A checkpoint: [memory/phase2-issue1a-checkpoint.md](../../memory/phase2-issue1a-checkpoint.md)
