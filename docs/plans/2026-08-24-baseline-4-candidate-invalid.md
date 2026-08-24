# Baseline-4 candidate — INVALID / ABORTED（诊断档案）

> 日期：2026-08-24 · 状态：**INVALID — 不进入 baseline 谱系，仅作诊断工件**
> 原因：question channel 名义开启但实际覆盖率 0.94%（13/1381 chunks），实验变量未有效施加

## 为什么 INVALID

Task 17 按 plan §Task 17 执行（`.env` 切 `QUESTION_CHANNEL_ENABLED=true` → 删除重传 → integrity），integrity 硬断言（`chunk_questions > 0`）通过，但事后核查发现：

| 指标 | 设计预期 | 实际 |
| --- | --- | --- |
| 有问题向量的 chunks | ~1340 全部（每 chunk 4-5 问） | **13 / 1381** |
| chunk_questions 行数 | ~5000-6500 | **62** |
| 按文档分布 | 三份均衡 | 2025→50 / 2024→12 / **2023→0** |

62 条问题向量在 RRF 权重 0.15 下影响趋近于零——此时跑任何评测，测的都是"channel ON @ 1% 覆盖"≈ Baseline-3 复跑 ± noise。升高了会误证 channel 有效，降低了会误证有害，两个结论都是假的。**不跑 eval，直接判 INVALID。**

## 根因（P1 ingestion reliability defect，已两次影响实验语料）

[app/ingestion/metadata.py](../../app/ingestion/metadata.py) 对整份文档做**一次** LLM 调用生成全部 chunk 的 title/summary/questions：

```text
一次调用 × 474 chunks = large structured generation request
    ↓ input size ↑ + output size ↑
timeout / JSON 截断
```

三次实锤：
1. Baseline-3 重摄轮：metadata timeout → 41 chunks 整块丢失（1340 vs 1381）
2. Task 17 本轮：2023 文档调用重试后仍超时 → 436 chunks 零 metadata
3. Task 17 本轮：2024/2025 响应 JSON 截断 → 仅 12/50 个 chunk 拿到问题

## 修复方向（进行中）

架构修复而非临时 patch：metadata 生成改为 **25 chunks/批 + 每批 ≤3 次尝试 + 失败批降级为空 metadata 继续入库**；批内映射必须显式键回显、禁止位置对位。约束：只改 metadata 可靠性，不动 chunk 边界/文本/embedding/检索。

## 后续正确实验链（用户裁定）

```text
Baseline-3 (LOCKED, 73.8% @65Q)
1340 chunks · question OFF        ← corpus 不完整（-41），仅历史参照
        │
Baseline-3R   ← 重建控制组
1381 chunks · question OFF · metadata batching fixed
        │  唯一变量：question channel
Baseline-4
1381 chunks · question ON · coverage gate ≥90%（overall + 每文档 >0）
        │
65-Q benchmark
```

关键纪律：
- **3R 是控制组不是新功能实验**——没有它，+41 chunks 恢复与 channel 开关两个变量混在一起无法归因
- integrity 验收升级：`chunks == 1381`（或与 clean reference 的 chunk ID 集合一致）+ `chunk_questions > 0` + **coverage ≥90% 且三份文档均 >0**（防"整体 95% 但某年度全零"的倾斜）
- 本档案保留 Task 17 全部数字作 diagnostic artifact，lineage 图上一眼可见该实验发生过但不支撑任何结论

## Task 17 过程数据（diagnostic artifact）

- 文档 IDs：2023 `57bc45c12ec74ca9` / 2024 `9c27936702d946aa` / 2025 `6a3baac82ed84f6c`
- 全部 indexed，436+471+474=1381 chunks，v3 全标记，table chunks 66 (4.8%)
- 耗时 ~25 min（2023 因 metadata 重试独占 ~24 min）
- integrity report：硬断言全过（正因只断言 >0 才漏掉覆盖率问题）
- 后端保持运行，flag=true 生效中

## 关联

- 锁定档：docs/plans/2026-08-24-baseline-3-locked.md
- 会话计划：docs/plans/2026-08-24-next-steps.md
- Memory: rag-v2-small-sample-gate-noise.md / rag-v2-mixed-version-lesson.md