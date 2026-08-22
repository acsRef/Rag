# Phase 4-D Gate Pilot Report (2026-08-23)

> **结论**：❌ **Evidence Gate KEEP DISABLED / DEFER**（不是 DELETE）— 实测净效果负值（0 recovery + 3 false refusal），但保留代码作为未来可复用基础设施
>
> 目标：验证修复后的 detector + severity-aware gate 是否产生有价值的 Gate 信号
> 数据：[`eval/ablation/20260822T164335/`](../ablation/20260822T164335)（gitignored）

---

## 1. 实验配置

- **Model**: `CHAT_MODEL=deepseek-ai/DeepSeek-V3`（V3）
- **Dataset**: 10 题子集（Q01-Q10，三一重工年报）
- **Configs**: off / t=0.5 / t=0.7 / t=0.9（4 个阈值）
- **Detector**: Phase 4-B 重写后的 ConflictDetector
- **Gate**: Phase 4-C severity-aware（仅 high severity 触发拒答）
- **Output**: `eval/ablation/20260822T164335/`

## 2. 实验结果

| Config | Accuracy | Refusal | False Refusal | p50 | p95 |
|---|---|---|---|---|---|
| off (Baseline-1) | **40%** | 0% | 0% | 9.6s | 32.1s |
| t=0.5 | 10% | 40% | 40% | 7.5s | 11.2s |
| t=0.7 | 40% | 40% | 40% | 8.5s | 9.9s |
| t=0.9 | 20% | 40% | 40% | 8.8s | 11.7s |

**Per-question（off config）**：

| Q | Off | T05 | T07 | T09 | 备注 |
|---|---|---|---|---|---|
| Q01 | ❌ | REFUSED | ❌ | ❌ | detector 误判 → Gate 拒答 |
| Q02 | ✅ | REFUSED | ✅ | REFUSED | off 答对，Gate 误拒 |
| Q03 | ❌ | REFUSED | REFUSED | REFUSED | off 答错（gold 与模型对不上）|
| Q04 | ❌ | ❌ | ❌ | REFUSED | detector 误判 |
| Q05 | ✅ | ✅ | ✅ | ✅ | — |
| Q06 | ✅ | ✅ | ✅ | ✅ | — |
| Q07 | ❌ | ✅ | REFUSED | ✅ | 模型答了部分，gold 漏 |
| Q08 | ✅ | ✅ | ✅ | ✅ | — |
| Q09 | ❌ | REFUSED | REFUSED | REFUSED | detector 误判 |
| Q10 | ❌ | ❌ | ❌ | ❌ | — |

## 3. Pairwise 分析

| Pair | Recovery | Replaced | Ratio | 含义 |
|---|---|---|---|---|
| off vs t=0.5 | 0 | 3 | 0.0 | Gate 拒答的 3 题在 off 时都答对 → false refusal |
| off vs t=0.7 | 1 | 3 | 0.25 | 1 题 Gate 拒答且 off 时也答错（partial recovery），3 题 false refusal |
| off vs t=0.9 | 0 | 3 | 0.0 | 与 t=0.5 相同 |

**关键解读**：所有 4 个 Gate config 都有 **3 个 false refusal**（拒答了 off 时答对的题）。Gate 没能救任何题，反而白白拒答了正确题。

## 4. 与 Baseline-1 (60%) 对比

注意：本次 off config 准确率 = 40%（不是 Baseline-1 的 60%）。

**原因**：V3 LLM 非确定性。同样的代码 + 同样的题，不同 run 可能给出不同答案。

**含义**：
- Baseline-1 = 60% 是单次采样结果，不是 stable 基线
- 真值应在 40-60% 区间内（judge noise + LLM noise 都贡献方差）
- **pairwise 比较仍然有效**（同一 run 内 model 输出相同，gate 决策影响清晰）

## 5. 接受门槛验收

来自 [docs/plans/2026-08-23-phase4-evidence-contract-repair.md §4-D](2026-08-23-phase4-evidence-contract-repair.md)：

| 指标 | 门槛 | 实际（任一 config） | 通过？ |
|---|---|---|---|
| accuracy | ≥ 50% | 最高 40% (off, t=0.7) | ❌ |
| false_refusal_rate | ≤ 20% | 40%（所有 gate config）| ❌ |
| refusal_rate | ≤ 30% | 40%（所有 gate config）| ❌ |
| latency p95 overhead | < 50% | off p95=32s，gate p95≤12s（gate 更快因为拒答不生成）| ✅ |

**Phase 4-D FAILED**：3 项硬门槛全部未通过。

## 6. 根因分析

**Q01 / Q04 / Q09 detector FP 模式**：

detector 在中文报告里常见的「**多 base metric 共享一个 action word**」场景失效：

```
「营业总收入 783.83 亿元，同比增长 5.9%」
「归属母公司净利润 59.75 亿元，同比增长 31.98%」
「电动搅拌车销量同比上升 47%」
```

`同比上升/同比增长` 这个 action word 距离 value 最近，extractor 把它当作 metric，但实际 value 是不同 base metric 的增长率。

要彻底解决需要：
1. **LLM-based extraction**（成本高、延迟高）
2. **更复杂的句法分析**（如依存句法）
3. **base metric + action word 组合作为 metric**（如「营业总收入 同比增长」）

## 7. 结论

**Evidence Gate 在 Phase 4-B/C 修复后仍然 net-negative**：
- ✅ Detector FP 从 9/10 降至 4/10
- ❌ 剩余 4/10 FP 足以让 Gate 误拒答
- ❌ Pairwise 显示 0 recovery + 3 false refusal
- ❌ 所有 Gate config 都未通过接受门槛

**实际价值**：
- Detector 修复是 **基础改进**（未来 LLM extractor 接入会更容易）
- Severity-aware gate 是 **正确的方向**（避免 medium severity 误拒答）
- 当前 Gate 实现不能上线，需要更可靠的 detector（LLM-based）

## 8. 决策

### Evidence Gate 状态：**KEEP DISABLED / DEFER**（不是 DELETE）

**不是 DELETE 的理由**：
- 当前证明的是「**当前 Evidence Gate + ConflictDetector 架构没有价值**」
- **没有**证明「所有 evidence sufficiency gate 都没有价值」
- Phase 4 代码可复用：ConflictKey 数据契约 + ConflictKeyExtractor + severity-aware 决策模式 + structured evidence findings
- 这些可能成为未来其他机制的基础
- `query_type` 是 dead implementation（DELETE 合理）；Evidence Gate 有完整实现 + 真实 ablation + 当前 ROI negative → DEFER 更合适

**代码可保留 + runtime 默认关闭**（即 evidence_gate_enabled = False）；不维护成活跃策略，但保留基础设施。

### LLM-based ConflictDetector：**Future/Research candidate**（不在当前 roadmap）

剩余 4/10 FP → 引入 LLM → 增加 latency/cost/failure mode → 再做新 contract → 再 eval，**ROI 很差**。当前阶段不做。

### Engineering conclusion

> **Evidence Gate was evaluated after repairing its upstream conflict-detection contract. The repaired detector reduced false positives from 90% to 40%, but the gate still produced zero recovery and multiple false refusals in the pilot; therefore the gate is retained as disabled infrastructure and deferred rather than enabled in production.**

## 9. 决策建议

**选项 A**（推荐）：保持 Gate off，把 Phase 4 的 detector 改进 + structured findings 沉淀，转向 Judge calibration + Issue #1-B
- 优点：稳定 40-60% accuracy（LLM noise 区间）；detector 修复可被未来 LLM-based extraction 复用
- 缺点：Gate 价值仍未验证（DEFER 而非 ENABLE）

**选项 B**：继续完善 detector（LLM-based extraction）— **暂不做**
- ROI 很差（详见 §8）

**选项 C**：完全跳过 Gate，专注 Issue #1 系列
- 与 A 重叠，A 更系统化

**采纳 A**：保持 Gate off + DEFER，下一阶段 Judge calibration + Issue #1-B。

## 10. Baseline 状态（Phase 4 完成后）

| 项 | 值 |
|---|---|
| 测试基线 | `468 passed / 6 failed / 13 skipped`（unit 454/2）|
| ConflictDetector FP rate | 4/10（从 9/10 改善，剩余 4 个 regex 难解）|
| Evidence Gate 状态 | **KEEP DISABLED / DEFER**（详见 §8）|
| Baseline-1 (V3 + Issue #1-A + Gate off) | 40-60%（单次 60%，LLM non-determinism + judge noise）|

## 11. 关联

- Phase 4 plan: [docs/plans/2026-08-23-phase4-evidence-contract-repair.md](2026-08-23-phase4-evidence-contract-repair.md) §4-D
- P0 audit: [docs/plans/2026-08-23-p0-audit-report.md](2026-08-23-p0-audit-report.md)
- Spec: [docs/plans/2026-08-23-conflict-key-spec.md](2026-08-23-conflict-key-spec.md)
- Memory: [memory/phase4-evidence-contract-repair.md](../../memory/phase4-evidence-contract-repair.md)
- Output: `eval/ablation/20260822T164335/`（gitignored）
