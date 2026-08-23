# Baseline-1R — Replay 锁定文档

> 日期：2026-08-23 · Commit `80505fa` 后 · **状态：LOCKED**
> 角色：Step 1（embedding 配置迁移）的唯一同口径参照点。Baseline-2 的 gate 全部对照本文档。

## 配置快照（测量条件）

| 项 | 值 |
| --- | --- |
| 语料库 | dev 库旧语料（1381 chunks / 3 文档，vector(4096)，摄入早于 question channel） |
| embedding | Qwen/Qwen3-VL-Embedding-8B @ 4096（config 默认，未改） |
| question channel 有效行为 | OFF（`chunk_questions = 0`，配置 true 但数据缺失的历史状态——见设计文档 §1 隐藏变量一） |
| evaluator | locked v1（judge = eval_sany.JUDGE_PROMPT + judge_answer，DeepSeek-V3；gold 经 eval.gold verified_only=True，15 题） |
| harness | eval/baseline_eval.py @ 300s 超时（`80505fa`） |
| 后端 | 同一进程贯穿全程；health OK |

## 结果（2026-08-23T13:03Z）

**overall acc(≥2) = 50.0%（7/14 scored）· mean = 50.0% · 覆盖 14/15**

| 类别 | acc(≥2) | n_scored/n | 明细 |
| --- | --- | --- | --- |
| A 单文档事实 | 33.3% | 3/3 | Q01=2, Q02=1, Q03=0 |
| B 表格理解与单位换算 | 0.0% | 2/2 | Q11=0, Q12=0 |
| C 跨文档对比 | 0.0% | 2/2 | Q17=0, Q18=1 |
| D 计算与多跳推理 | 100% | 1/2 | Q25=3（Q26 未评分） |
| E 时序与追溯调整 | 50.0% | 2/2 | Q32=3, Q33=0 |
| H 错误前提纠偏 | 100% | 2/2 | Q50=3, Q51=2 |
| I 拒答与知识边界 | 100% | 2/2 | Q55=3, Q56=3 |

## 测量过程记录（两次运行）

1. **Run 1（作废）**：harness 超时 180s。Q17/Q33 客户端 Read timed out（复杂题触发 rewrite 慢路径+长生成）。后端全程健康 → 判定测量工具时限过紧，修至 300s（commit `80505fa`），Run 1 结果不作为任何参照。
2. **Run 2（锁定即本表）**：14/15 完成。**Q26 传输失败**——服务端检索 953ms 正常完成、生成调用已发出（HTTP 200），随后流式阶段静默挂起约 5 分钟（SiliconFlow 上游停摆），客户端 300s 超时。首跑同题曾得 3 分，非能力缺陷。

**不重跑刷分的理由**：Q26 的传输失败与 judge 抽样耦合——选择性重跑会在修复传输的同时重抽 judge 打分（两轮间实测波动：Q02 2→1、Q25 2→3、Q32 1→3），违反"复合变更 ≠ 单变量归因"的同源精神。锁定最完整的单轮。

## 使用规则（给 Baseline-2 gate）

- 对照口径：同题集、同 evaluator、n_scored 显式排除未评分题（summary 自描述）
- **噪声底**：单题翻转 ≈ 7pp（1/14）。gate 阈值 −10pp 仅略高于单题噪声——判定时必须同时看分类别明细，单一类别 ±1 题不构成"系统性崩塌"
- 与历史基线不可直比：65 题 78.3%、V3 10 题 60% 均为不同 population / 不同 gold 版本 / 修正前口径
- verified 15 题集刻意包含难例（Q33 边界题、修正 gold Q26/Q55），50% 不代表全量水平

## 谱系位置

```text
Historical（仅参考）→ ★ Baseline-1R（本档，旧配置 replay）
    ↓ Step 1: Qwen3-Embedding-8B@1024 + channel off
Baseline-2（待测）
```
