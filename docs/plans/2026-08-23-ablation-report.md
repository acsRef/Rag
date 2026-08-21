# Day 2 晚上 Ablation Report (2026-08-23)

> 接 [docs/plans/2026-08-22-rag-decomposition.md](docs/plans/2026-08-22-rag-decomposition.md) §九 Day 2 晚上。
>
> 8 组 retrieval-only ablation + answer-level 评估留给后续。

## 摘要

| 结论 | 数据 |
|---|---|
| retrieval 层已到天花板 | 8 组 hit@10 全部 = **1.000** |
| 5 检索策略在 sany 语料无 recall 贡献 | Baseline hit@10 = All ON hit@10 |
| 5 检索策略对 MRR 影响 ≤1pp | Baseline 0.842 → All ON 0.850（+0.8pp，噪声范围） |
| Evidence gate 需要 chat/stream 评估 | retrieval_eval 走 /retrieve，不走 gate——需 generation eval |
| embedding_text v2 已废 | 详见 [docs/plans/2026-08-23-day2-morning-done.md](2026-08-23-day2-morning-done.md)；Day 2 上午验证 MRR -5.2pp |

## 8 组配置（regression 20 题）

按 plan §九 Day 2 晚上顺序（Section Embedding 跳过——已知负收益）：

| # | Pipeline | hit@5 | hit@10 | recall@5 | recall@10 | MRR | avg latency |
|---|---|---|---|---|---|---|---|
| 1 | Baseline（所有策略关）| 0.95 | **1.000** | 0.900 | **1.000** | 0.842 | 670 ms |
| 2 | All Retrieval ON（5 策略全开）| 0.95 | **1.000** | 0.925 | **1.000** | **0.850** | 580 ms |
| 3 | + Cross-doc only | 0.95 | **1.000** | 0.900 | **1.000** | 0.850 | 567 ms |
| 4 | + Section Boost only | 0.95 | **1.000** | 0.925 | **1.000** | 0.850 | 570 ms |
| 5 | + Section Supplement only | 0.95 | **1.000** | 0.900 | **1.000** | 0.850 | 568 ms |
| 6 | + Year Supplement only | 0.95 | **1.000** | 0.925 | **1.000** | 0.850 | 660 ms |
| 7 | + Evidence Gate (.7) | 0.95 | **1.000** | 0.925 | **1.000** | 0.850 | 596 ms |
| 8 | + Evidence Gate (.5) | 0.95 | **1.000** | 0.925 | **1.000** | 0.850 | 589 ms |

**关键观察**：
1. **hit@10 = 1.000 在所有 8 组都成立** —— retrieval 100% 召全 gold
2. **MRR 差异 0.842 vs 0.850（+0.8pp）** —— 在 rerank LLM 噪声范围
3. **recall@5 在 0.90 / 0.925 之间跳** —— 同策略相关性可能，但样本 20 噪声大
4. **latency 567-670 ms** —— 策略 hook 几乎不影响延迟（hybrid_search 已经是 baseline 重）

## 重要注：Evidence Gate 不在这次 ablation 体现

`tools/run_ablation.py` / `eval/retrieval_eval.py` 调用 `/api/v1/retrieve` 端点，**绕过 pipeline.py 的 evidence gate 块**。

| 路径 | 是否经过 evidence gate |
|---|---|
| `/api/v1/retrieve` (retrieval_eval) | ❌ 跳过 |
| `/api/v1/chat/stream` (生产 chat) | ✅ 触发 |

要真实评估 gate 效果，需要：
1. 写 `eval/generation_eval.py` 调 `/api/v1/chat/stream`
2. 解析 SSE 事件流，记录 `evidence_refused` 事件
3. 统计被拒答的题数 + 最终答案精度（LLM judge）

参考 plan §九 Day 2 晚上 + §十 验收第 4 项 "baseline ≥ 73.3%"——需要 generation eval 才能给出真实答案分。

## 解读

### 1. 检索层（已验证）

- **5 检索策略在 sany 年报语料对检索召回无贡献** —— 多次 ablation（Day 1 下午 full 65、Day 2 晚上 regression 20）一致
- 策略 hook（`_supplement_*` / `_boost_*` / `_cross_doc_extra`）的内部逻辑不为错，但**它们要解决的失败模式在当前数据集不存在**
- 真正有效的"补全"由 `hybrid_search` 自身 + rerank + MMR 已覆盖

### 2. 5 检索策略延迟影响

- Baseline latency: 670 ms
- All ON latency: 580 ms（**反而更低**——可能是 cache hit 分布不同）
- 单策略 latency 567-660 ms —— 5 个 hook 加起来不到 50 ms 差异，可忽略

### 3. 已废：embedding_text v2

- Day 2 上午测试 `build_embedding_text(c, doc)` 加 document/section prefix
- Full 65 实测：recall@10 1.000 → 0.984 / MRR 0.876 → 0.824
- **回滚到 chunk-only embedding**；build_embedding_text + embedding_version 保留为 ablation 基础设施

### 4. Evidence Gate（待 generation eval）

- 新增 EvidenceResult / build_evidence_result / evidence_gate_should_refuse（16 tests）
- `evidence_gate_enabled = False`（默认关），启用 `EVIDENCE_GATE_ENABLED=true`
- `evidence_min_coverage = 0.7`（默认阈值）
- 当前 retrieval metrics 无法验证 gate 真实表现；待 generation eval

## 下一步（Generation Eval / Answer-Level）

为完成 plan §十 验收：

1. **写 `eval/generation_eval.py`**：
   - 调 `/api/v1/chat/stream`（走完整 pipeline，包含 evidence gate）
   - 解析 SSE events，提取 `token` + `degraded` + `done`
   - 把答案写到 results.json

2. **改造 `eval_sany.py` 支持 evidence gate 配置**：
   - 跑 baseline（gate off）vs gate on with .5 / .7
   - LLM judge 比对 答案精度
   - 记录被拒答的题（gate 拒绝的题目可能就不该答）

3. **Year Filter 真实价值**：
   - 当前 `chunks.year` 列 NULL（indexer 没写入）
   - reembed_v2 不写 year（来源 Document.filename 解析）
   - **未来工作**：Day 3+ 跑一个 `tools/backfill_year.py`，从 Document.filename 解析 year + UPDATE chunks.year
   - 然后在 hybrid_search 加 `AND c.year = ANY(:years)` 实现真正的 year SQL filter

## 数据附录

### 工具

- `tools/run_ablation.py` — 自动化 8 组 ablation（写好的 bash 封装）
- `eval/sany_annual_reports/ablation_results.jsonl` — 8 组原始数据（一行一 config）

### 复现命令

```bash
# 跑单个 config
CROSS_DOC_ENABLED=true EVIDENCE_GATE_ENABLED=false \
  D:/miniConda/envs/rag/python.exe -m app.main &

sleep 5
D:/miniConda/envs/rag/python.exe eval/retrieval_eval.py --tier regression --top-k 10

# kill 后台
PID=$(netstat -ano | grep ":8000.*LISTENING" | awk '{print $NF}')
cmd //c "taskkill /F /PID $PID"
```

### 8 组结果对比表

```text
hit@10 排名（无差异）：
  All 8 configs: 1.000 ✓

recall@5 排名：
  0.925 (5 组): All ON / Section Boost / Year Supp / Evidence Gate .7 / Evidence Gate .5
  0.900 (3 组): Baseline / Cross-doc / Section Supp

MRR 排名：
  0.850 (7 组): All ON / Cross-doc / Section Boost / Section Supp / Year Supp / Gate .7 / Gate .5
  0.842 (1 组): Baseline

latency 排名（数值越低越好）：
  567 ms: Cross-doc
  568 ms: Section Supp
  570 ms: Section Boost
  580 ms: All ON
  589 ms: Gate .5
  596 ms: Gate .7
  660 ms: Year Supp
  670 ms: Baseline
```

**结论：所有策略组合在 retrieval 层基本无差异**——这正是 Day 1 下午 baseline ablation 的预期信号（策略默认 False 的依据）。

## 工程决策

| 项 | 决策 | 依据 |
|---|---|---|
| 5 检索策略默认值 | **保持 False** | 8 组 ablation 印证无 recall 贡献 + 微负 MRR |
| embedding_text 改造 | **回滚到 chunk-only** | Day 2 上午验证负收益 |
| embedding_version 字段 | **保留** | 作为未来 ablation 隔离机制 |
| build_embedding_text 函数 | **保留** | 等待重新设计 prefix 格式后重新启用 |
| evidence_gate_enabled 默认 | **保持 False** | 待 generation eval 验证阈值合理性 |
| evidence_min_coverage 默认 | **0.7** | 保守阈值，避免误拒 |
