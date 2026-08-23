# 下一步计划 (2026-08-23) — Evaluator v1 收口版

> **方法论纪律**（[memory/eval-methodology-feedback-2026-08-23.md](../../memory/eval-methodology-feedback-2026-08-23.md)）：
> - 复合变更 ≠ 单变量归因
> - 单 agreement ≠ correctness（需 3 轴：Agreement / Accuracy / Shared Error）
> - Gold quality ≥ Evaluator quality（gold_evidence + gold_judgment 防 gold 自错）
> - 不预设准确率目标，用 acceptance criteria

## 本日完成总览（2026-08-23）

| 阶段 | 产出 | Commit |
|---|---|---|
| Issue #1-A | complexity 清理 5 commits, Baseline-1 V3 40-60% | 86b5daa..8a7bb34 |
| P0 双审计 | Judge noise 10-20% + ConflictDetector ≈100% FP | c7f81c5 |
| Phase 4 (4-A/B/C/D) | ConflictKey 契约 + detector FP 9→4/10 + severity gate + pilot net-negative | 8e59bf5, eba1780, a324cdc |
| Gate 决策 | **KEEP DISABLED / DEFER**（不是 DELETE） | b1120c8 |
| P0 Judge calibration | 15 题分层抽样 + V3 judge 跑分 + artifact 落盘 | bb43497 |
| PDF 原文交叉验证 | **发现 Q26/Q55 gold 错误**（gold corruption） | bb43497 |
| D: gold 统一 | rag_testset.json v2 schema + 只有 Q26/Q55 真修正 | 1808c37 |
| A: rubric v1 | Q33 claim decomposition → Wrong; rubric 三档固化 | c7a4eb9 |
| B: deterministic numeric | claim-level 3 层验证 + 9 单测 | daac785 |
| C: evaluator modularization | eval/gold.py + eval/judge_rubric.py + 24 单测 | 494eec1 |

## LOCK 条件核对（per user spec）

| 条件 | 状态 |
|---|---|
| Gold: verified + single source of truth | ✅ rag_testset.json v2 (15 verified / 50 marked) |
| Judge: human agreement | ✅ 93.3% (14/15) |
| Judge: verified-ground-truth accuracy | ✅ 86.7% (13/15) |
| Shared errors 明确记录 | ✅ 2 real (Q12 false refusal, Q17 hallucination) + rubric 复核记录 |
| Rubric: Q33 边界已定义 | ✅ judge-rubric-v1.md + eval/judge_rubric.py RUBRIC_TEXT |
| Deterministic: numeric gate | ✅ 3 层验证（direct_hit / derived / not_found），citation check 未做 |
| Tests: baseline 守口 | ✅ 546 passed / 6 failed (= 预存集) / 13 skipped |

## 结论

**Evaluator v1 可以 LOCK**（条件全满足；citation deterministic check 是 v2 增强，不阻塞 LOCK）。

LOCK 含义：
- judge prompt / score_to_verdict / collapse 规则冻结
- 后续实验（prompt 改动、模型对比、65 题正式 benchmark）全部用同一 evaluator
- evaluator 自身改动需要新版本号 + 重新校准

## 当前项目状态树

```
Phase 0 → Phase 1 → Phase 2A → Issue #1-A → Phase 4 → Gate DEFER
    ↓
★ CURRENT: Evaluator v1 LOCKED
    |
    +── P0: Issue #1-B (prompt modularization + tests, GAP-07+GAP-09)
    |
    +── P1: Issue #1-C (citation runtime validation)
    |        └── 可复用 eval/deterministic_numeric.py 的思路做 citation check
    |
    +── P1: Issue #1-D (policy boundary)
    |
    +── P2: B2 query_type DELETE / Table-aware / Model routing / MCP
    |
    +── P2: 65 题正式 benchmark (用 locked evaluator v1 跑)
    |
    +── Future: citation deterministic check / LLM-based ConflictDetector
```

## 下一步优先级

### P0 — Issue #1-B
Prompt modularization + tests（GAP-07 + GAP-09）。`app/core/prompt.py` SYSTEM_PROMPT 是单字符串无单测；拆成结构化模块后每段规则可独立测。注意：改动 prompt 文本 = 触发新 baseline，需重跑 10 题。

### P1 — Issue #1-C / #1-D
- 1-C: citation runtime validation（代码层校验 [N] 引用）
- 1-D: policy boundary（Gate vs Refusal Policy 分工文档化）

### P2 — 队列
B2 query_type DELETE → Table-aware ingestion → Model routing → MCP → 65 题 benchmark

## 不变量

- 测试基线 `546 passed / 6 failed / 13 skipped`（6 failed 集合禁止新增）
- Evidence Gate KEEP DISABLED
- Evaluator v1 LOCKED（judge prompt / rubric / collapse 不再改）
- Gold 单一入口 `eval.gold`（不允许直接 open rag_testset.json）
- 每次修改独立 baseline

## 关联文档

- Gold correction: docs/plans/2026-08-23-gold-correction-report.md
- Rubric v1: docs/plans/2026-08-23-judge-rubric-v1.md
- Phase 4 pilot: docs/plans/2026-08-23-phase4-d-gate-pilot-report.md
- Memory: phase4-evidence-contract-repair.md / gold-correction-report-2026-08-23.md / eval-methodology-feedback-2026-08-23.md
