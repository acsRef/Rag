# 下一步计划 (2026-08-24) — Baseline-3 LOCKED，Task 17 question channel 待启

> 父计划：[2026-08-23-rag-v2-implementation-plan.md](2026-08-23-rag-v2-implementation-plan.md) · 锁定档：[2026-08-24-baseline-3-locked.md](2026-08-24-baseline-3-locked.md)

## 本次完成总览（2026-08-24）

| 任务 | 产出 | Commit |
|---|---|---|
| Task 14 | build_retrieval_text() + 9 unit tests（TDD + spec/code review 双轮） | `d0e1628` / `93f1b07` / `3745fe1` |
| Task 15 | indexer 接线（embed 输入/search_text/5 元数据列）+ Phase C 混版本不变量测试 | `53742c5` |
| Task 16 | 重摄 3 PDF（1340 chunks v3）+ integrity PASSED + 15-Q run1 → C 塌 Gate FAIL | — |
| Task 16b | 15-Q run2 → C 回 B 塌（Branch C 大面积噪声，证伪 15-Q gate 统计力） | — |
| Task 16c | **升级 65-Q 全量 benchmark**：overall 73.8%，六准则全过 → **Baseline-3 LOCKED** | `2f1c0f9` |

## Baseline-3 正式分数（65 题 × evaluator v1）

**overall acc(≥2) = 73.8%（48/65）· mean 2.23/3 · 覆盖 65/65**

| 类别 | n | acc | 类别 | n | acc |
|---|---|---|---|---|---|
| A 单文档事实 | 10 | **80.0%** | F | 6 | 66.7% |
| B 表格理解 | 6 | **66.7%** | G | 6 | 100.0% |
| C 跨文档对比 | 8 | **50.0%** | H 错误前提 | 5 | 80.0% |
| D 计算多跳 | 7 | 71.4% | I 拒答边界 | 5 | 100.0% |
| E 时序追溯 | 6 | 66.7% | J | 6 | 66.7% |

分面：answerable 70.9% / refusal(H+I) 90.0% / numeric(B+D) 69.2%

## 方法论记录（重要）

15-Q gate 被 ±50pp 单题翻转击穿（run1 C 塌 / run2 B 塌，方向相反）→ 升级 65-Q 判定。教训已入 memory：`rag-v2-small-sample-gate-noise.md`。处置序：查 retrieval 诊断 → 重跑看方向 → 方向相反即升级大样本。

历史 78.3%（旧 evaluator+4096 配置）仅定性参考不可直比。

## 遗留项（不阻塞 Baseline-4）

1. **metadata-enrichment LLM timeout 加固**：重摄时丢 41/1381 chunks（1340 落库），根因 `app/ingestion/metadata.generate` TemporaryError 后整块丢弃
2. **C 类系统性弱点**：跨三年对比缺 2024/2025 年数据（8 题中 5 题此模式）——检索层多文档聚合问题，候选方案：query decomposition 重审 / cross_doc channel
3. **B 类单位换算滑移**：千元/亿元换算错误集中（LLM answer-extraction 层），非检索问题

## 下一步优先级

### P0 — Task 17/18（question channel 开闸 → Baseline-4）
按 plan §Task 17-18：
1. `.env` 切 `QUESTION_CHANNEL_ENABLED=true` + 重启后端
2. 删除重传 3 PDF（绕 hash 复用；本轮每 chunk 多跑 4-5 问 embed，耗时比上轮长）
3. integrity report `--expect-questions positive`
4. **gate 直接用 65-Q**（15-Q 已被证伪，不再用其做 lock 判定）；对照 Baseline-3 的 73.8%
5. 锁定 `docs/plans/2026-08-24-baseline-4-locked.md`

### P1 — metadata timeout 加固（41 chunks 丢失根因）
retry/backoff 或失败块降级保留裸 text，防止静默丢语料

### P2 — 收官
Task 19 归档 `baselines/final-65q/`（名字已保留）→ Task 20 会话收尾

## 不变量

- 测试基线 `571 passed / 6 failed (= 预存集) / 13 skipped`
- `.env` 当前 `QUESTION_CHANNEL_ENABLED=false`（Task 17 才切 true）
- dev 库：3 docs / 1340 chunks / embedding_version=3 / chunk_questions=0
- Evaluator v1 LOCKED；gold 经 eval.gold 单一入口
- 小样本 gate 处置序见 memory（不再用 15-Q 做 lock 判定）
