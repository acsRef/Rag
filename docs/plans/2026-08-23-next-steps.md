# 下一步计划 (2026-08-23) — Phase 4 立项版

> **方法论纪律**（[memory/eval-methodology-feedback-2026-08-23.md](../../memory/eval-methodology-feedback-2026-08-23.md)）：
> - 复合变更 ≠ 单变量归因
> - judge noise 必须与 detector 同等级关注
> - 不预设准确率目标，用 acceptance criteria
> - **Gate 实验必须在 detector + judge 都干净后再做**

## 本次会话完成

| 阶段 | 产出 |
|---|---|
| 1. Issue #1-A 执行 | 5 commits (86b5daa..8a7bb34)，ruff format 跳过（无变更） |
| 2. 测试基线守口 | 468 passed / 6 failed / 13 skipped（不变） |
| 3. Baseline-1 锁定 | V3 60% on 10q（composite change +20pp，**不能单独归因到 complexity 删除**） |
| 4. P0 双审计 | Judge 10-20% noise + ConflictDetector ≈100% false-positive |
| 5. Phase 4 立项 | 见下方 |

## 当前状态总览

```
Baseline-0
V3: 40% (历史 V3 + 旧 prompt + 旧 contract)
        ↓
Issue #1-A (composite)
        ↓
Baseline-1
V3: 60% (历史 V3 + 新 contract) — 真值 60% ± 15pp (judge noise)
        ↓
★ CURRENT (2026-08-23)
        |
        +── Phase 4-A: Conflict key 契约重定义 ← 现在起点
        |
        +── Phase 4-B: Parser / normalizer 修复
        |
        +── Phase 4-C: Severity-aware Gate
        |
        +── Phase 4-D: 10q Gate pilot
        |
        +── Judge human_verified subset (15 题手验)
        |
        +── 65q 全量 ablation (Phase 4-D 通过后)
```

## 下一步优先级

### P0 — Phase 4 (顺序执行)

1. **Phase 4-A: Conflict key 契约重定义** — 不写代码先写 spec
   - 新模块 `app/core/conflict_key.py`
   - 定义 `ConflictKey(entity, metric, period, unit, scope)` 五元组
   - 设计提取接口（不实现）
   - 预期产出：1 个 spec 文档（markdown）+ 1 个 dataclass

2. **Phase 4-B: Parser / normalizer 修复**
   - 重写 `_METRIC_PREFIX_PATTERN` + `_VALUE_PATTERN` + `_normalize_metric`
   - 新单测：`tests/unit/test_conflict_detector.py`
   - 预期产出：5-6 个 commit（pattern 拆解 + 测试 + edge case）
   - **测试基线**：468 passed / 6 failed / 13 skipped 守口

3. **Phase 4-C: Severity-aware Gate**
   - 修改 `evidence.py:99, 118`
   - 决策契约：high severity → refuse，medium/low → pass
   - 新单测：`tests/unit/test_evidence_gate_severity.py`

4. **Phase 4-D: 10q Gate pilot**
   - V3 + Baseline-1 + Gate off / t=0.5 / t=0.7 / t=0.9
   - 通过门槛：false positive ≤ 2/10 + accuracy ≥ 50% + false_refusal ≤ 20% + p95 overhead < 50%

### P1 — Judge evaluator（与 Phase 4 并行）

5. **human_verified gold subset (15 题)**
   - 从 65 题抽 15 题（每类 A/B/C/D/E 至少 2 题 + 1-2 tricky 题）
   - 我手验，建 `eval/sany_annual_reports/human_verified_15.json`
   - 不改 judge 行为 — 保持现状确保 Baseline-1 vs Baseline-2 可比
   - 后续每次评测用这 15 题做 LLM judge vs human gold 的 agreement 校准

### P2 — 队列

6. Issue #1-B（modularization + tests，GAP-07 + GAP-09）
7. Issue #1-C（citation runtime validation，GAP-04）
8. Issue #1-D（policy boundary，GAP-01 + GAP-03 + GAP-08）
9. Issue #1-E（wording，GAP-02 + GAP-05）
10. B2（query_type DELETE）
11. Issue #2（table-aware chunking）
12. 65 题全量 re-test（Phase 4-D 通过后）

## Acceptance Criteria（替代 preset 目标）

| 项 | 门槛 |
|---|---|
| 测试基线 | `468 passed / 6 failed / 13 skipped` 守口 |
| ConflictDetector false positive | ≤ 2/10（基线 9/10） |
| Eval 改进 | accuracy 提升 ≥ 2pp AND 无回归 AND latency p95 overhead < 50% |
| 单变量 | 每次改动只能动一个变量 |
| 归因 | summary 必须明确指出 single-variable vs composite |
| Judgement | 每个 per-question 结论必须有可验证的 judge / human-verified 依据 |

## 关键证据修正

- **之前**：V3/Qwen + Gate=on → 90% refusal → 倾向 DELETE Gate
- **现在**：90% refusal 是 ConflictDetector ≈100% FP 污染，不是 Gate 价值问题
- **修正后**：Evidence Gate → **VALUE UNDETERMINED**（不是 DELETE）；ConflictDetector → REFACTOR REQUIRED

## 不变量（每次修改必守）

- 测试基线 `468 passed / 6 failed / 13 skipped` 必须守口
- 6 failed 预存失败集合禁止新增
- 每次修改独立 baseline（Baseline-0 → Baseline-1 → Baseline-2 → ... → Experiment），不混叠
- ConflictDetector 修复前不跑 Gate 实验
- Judge 不在 Phase 4 范围内动
- Eval 数据集为 10 题子集，全量 65 题保留作 Phase 4-D 后验证
