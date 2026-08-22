# 下一步计划 (2026-08-23)

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

**计划偏离**：
- **§3.2 prompt section header 删除**：plan §3.2 列出 line 170 但保留 section header `## 难度分类（控制 CoT 触发）`；考虑 §1「彻底清除 complexity」与 D2 无条件规则，整段（lines 168-180）一并删除。
- **§3.3 orphan comment**：line 335 `# 复杂度分类（控制 CoT 触发）：默认 complex（保守触发 CoT）` 在 line 336 变量删除后成 orphan，一并删。
- **§3.4.1 SYSTEM_PROMPT**：plan §3.4.1「示例」section 头 `【示例1 — 复杂问题】` 也删除（plan 的"删除后"已不含此行）。
- **§3.4.1 trailing blank line**：原 52-58 行删除范围，改为 52-58（含末尾 `\n`）以保持章节间距。
- **Commit 6 ruff format**：跑后无变更（5 个文件已符合格式），不创建空 commit。

### 测试基线（守口）

| 项 | 修复前 | 修复后 |
|---|---|---|
| 全量 pytest | `468 passed / 6 failed / 13 skipped` | `468 passed / 6 failed / 13 skipped` |
| 6 failed 集合 | 不变 | 不变 |
| ruff check | 全绿 | 全绿 |

> Plan 写的是 459 passed，实际已涨到 468（9 个新测试在 plan 之后加入）。失败集合严格守口。

### Baseline-1 锁定（V3 10 题小样本）

**Config**: `CHAT_MODEL=deepseek-ai/DeepSeek-V3` + Gate off + Issue #1-A applied + 10 题
**Output**: `eval/ablation/20260822T160235/`

| Metric | Baseline-0 | Baseline-1 | Δ |
|---|---|---|---|
| accuracy | 40% (4/10) | **60% (6/10)** | **+20pp** |
| refusal_rate | 0% | 0% | — |
| false_refusal_rate | 0% | 0% | — |
| latency_p50 | 8.9s | 9.0s | +0.1s |
| latency_p95 | 12.2s | 12.1s | -0.1s |

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
| Q10 | ❌ | ❌（**judge false-negative**） | A 类，答案 0.9834 元/股 与 gold 完全一致 |

> ⚠️ Q10 judge 误判（answer 与 gold 完全一致但 judge 判 incorrect）。可能 ±1-2 题 judge noise，但 +20pp 改善鲁棒（需 4 个 judge 错误才能反转结论）。

### Issue #1-A 仍存（出 plan 范围，待后续）

| 项 | 备注 |
|---|---|
| `KB_ANSWER_TEMPLATE` line 115「如为复杂问题」软提示 | Issue #1-E（wording）一并扫 |
| `tests/unit/test_rewrite_complexity.py` 文件名 | Issue #1-D 重命名（plan §3.5 注释） |
| ConflictDetector 高 temporal_consistent=False 噪声（9/10） | 独立调查，决定修 vs 保留 |
| `<think>` 协议层重构 | B/C 类，独立 plan |

## 关键决策固化

1. **Baseline-1 = 60% on V3 10q** 已锁定（见上表）。
2. **Issue #1-A delta 与 Baseline-1 强绑定**：5 个 commit 已是 master HEAD（8a7bb34）。
3. **回滚点**：Issue #1-A schema commit 86b5daa 是 Phase 2 A 类决策执行点，回滚 = revert commit。
4. **ruff format commit 跳过**：5 文件已符合 ruff 格式，无变更无 commit。

## 下一步优先级

### P0（立刻做）

1. **ConflictDetector 单查** — 9/10 temporal_consistent=False 高噪声是 Gate 接通的核心瓶颈。Issue #1-A 不修此项也能让 Baseline-1 涨到 60%，但 Gate 接入必须先解决 detector 可靠性。决定：
   - 修：若 detector 实际能过滤真冲突但误报多，需精修
   - 删：若 detector 当前实现无价值（per B-class 决策模式 DELETE 是合理结局）

2. **Issue #1-B**（modularization + tests）— GAP-07 + GAP-09。GPT-4o/Claude API 的 prompt 字符串无单测是当前测试覆盖率盲区。

### P1（视 P0 结果决定）

3. **Gate 重测（Baseline-1 + Gate）** — 仅当 ConflictDetector 修复后才做，否则仍是 90% refusal + 0% accuracy。
4. **Issue #1-C**（citation runtime validation）— GAP-04 prompt 是软约束，必须代码层校验 `[1][2]` 引用号是否对应实际 sources。
5. **Issue #1-D**（policy boundary）— GAP-01 + GAP-03 + GAP-08，定义 Evidence Gate vs Refusal Policy 分工。

### P2（队列）

6. **Issue #1-E**（wording）— GAP-02 + GAP-05；含 `KB_ANSWER_TEMPLATE` line 115 软提示清理 + 14 条 checklist 收口。
7. **B2**（query_type DELETE）— 独立 plan，commit 已决策但未执行。
8. **Issue #2**（table-aware chunking）— 排队等 Issue #1 收口。
9. **全量 65 题 re-test** — 当前 Baseline-1 只是 10 题样本，全量验证待 P0/P1 关键决策后做。

## 目标

Issue #1 完成后整体 V3 baseline 期望 ≥ 65%（10 题）；Issue #1 全部完成 + Gate 接通后 ≥ 70%（65 题全量）。当前从 40% → 60% 已 +20pp，距离 70% 目标还有 ~10pp，主要由 P0（ConflictDetector）+ P1（Gate）共同贡献。

## 不变量（每次修改必守）

- 测试基线 `468 passed / 6 failed / 13 skipped` 必须守口
- 6 failed 预存失败集合禁止新增
- 每次修改独立 baseline（Baseline-0 → Baseline-1 → Baseline-2 → ... → Experiment），不混叠
- Eval 数据集为 10 题子集（首个 10 个 A 类），全量 65 题保留作 P2 阶段验证
