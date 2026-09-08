# Baseline-3R — 控制组重建记录

> 日期：2026-09-06（commit `d085ec9`）· 状态：**控制组（Control Group），非功能实验**
> 补充说明：Baseline-3（2026-08-24 LOCKED）尚缺重建记录，本文件补齐其 lineage 尾环，并明确 3R 的定位。

## 为什么需要 3R

Baseline-4 候选被标记 INVALID 后（metadata 单次调用缺陷丢 41 chunks / question coverage 0.94%），实验链条出现一个缺口：

```text
Baseline-3 (LOCKED, 73.8% @65Q)   ← corpus 实际 1340 chunks（-41，历史参照）
        │
Baseline-3R   ← 重建控制组：1378→1381 chunks、metadata batching 修复、channel OFF
        │      唯一变量：question channel
Baseline-4（候选已 INVALID，重跑为后续实验）
```

没有 3R，"+41 chunks 恢复" 与 "question channel 开关" 混在一起无法归因——3R 是干净的、channel OFF 的基线参照点。

## 重建内容（commit `d085ec9`）

- 清库重摄：**1381 chunks**（2023/2024/2025 三份年报；Task 17 同源分解 436/471/474），全部标记 corpus 代际 v3
- question channel **OFF**（与 Baseline-3 一致；摄入侧/检索侧同关）
- metadata batching 修复生效（25 chunks/批 + 重试 + 显式键映射），不再丢 chunks / 丢 questions
- table chunks 66 (4.8%)，与 Baseline-3 比例一致

## 结果（15 题 verified gate，2026-08-24T20:02Z run）

| 指标 | Baseline-3R | 参照 |
| --- | --- | --- |
| acc(≥2) | 66.7%（10/15）| Baseline-2 同口径 66.7% |
| mean score | 68.9% | — |
| 覆盖 | 15/15 | ✓ |

3R 与 Baseline-2（同样 15 题、channel OFF、B3 修复前语料）数字一致，佐证其为"重建的控制组"而非新实验。65 题正式 benchmark 仍以 Baseline-3 的 73.8% 为锁定依据（65/65 覆盖）。

## 配置快照

| 项 | 值 |
| --- | --- |
| embedding | Qwen/Qwen3-Embedding-8B @ 1024 |
| current_embedding_version | 3 |
| question_channel_enabled | false |
| 完整性断言 | `tools/index_integrity_report.py --expect-documents 3 --expect-questions zero` → PASS |

## 后续

- Baseline-4（channel ON + coverage ≥90% 硬断言）保留为后续实验，**不阻塞本仓库封板**（见 Issue #3 Non-goal）。
