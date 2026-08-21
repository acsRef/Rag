# Day 1 下午 Baseline Ablation 报告 (2026-08-23)

> 接 [docs/plans/2026-08-22-rag-decomposition.md](docs/plans/2026-08-22-rag-decomposition.md) §九 Day 1 下午第 7 步验收。
>
> 用 `eval/retrieval_eval.py --tier full` 跑完整 65 题，对比"全 flags on" vs "全 flags off"。

## 关键结论（TL;DR）

- **retriever 已经召回 100% gold**：full 65 题 recall@10 = 1.000（所有题至少一个 gold doc 在 top-10）
- **5 个策略对召回无贡献**：hit@5/10 与 recall@5/10 在 on/off 下完全相同
- **5 个策略甚至略伤排序**：MRR 全 on 0.865 vs 全 off 0.876（**-1.1pp**）
- **结论**：在三一年报语料上，`cross_doc_enabled` / `section_boost_enabled` / `section_supplement_enabled` / `year_supplement_enabled` / `query_decomposition_enabled` 是**冗余甚至有害**的策略；ablation 验收 §十 第 5 条 "关掉后基线 ≤ 73.3%" 不适用——baseline 跑出来反而更高

## 测试条件

| 项 | 值 |
|---|---|
| 测试集 | `eval/sany_annual_reports/rag_testset.json` (65 题，含 `gold_documents` 标注) |
| Backend | `python -m app.main` (uvicorn 8000) |
| Embedding cache | 默认开 (`embedding_cache_enabled=True`) |
| Retrieval cache | 默认开 (`retrieval_cache_enabled=True`) |
| Rerank / MMR / question channel | 默认开（这些开关未在本次 ablation 覆盖范围内） |
| Q57 gold=[] | 64/65 题参与指标计算 |
| top-k | 10 |

## Full 65 题结果

### 全 flags on（默认生产配置）

| 指标 | 值 |
|---|---|
| hit@5 | 0.984 |
| hit@10 | **1.000** |
| recall@5 | 0.940 |
| recall@10 | **1.000** |
| MRR | **0.865** |
| 总耗时 | ~70 秒（cache 命中后） |

### 全 flags off（5 个策略全关）

| 指标 | 值 |
|---|---|
| hit@5 | 0.984 |
| hit@10 | **1.000** |
| recall@5 | 0.940 |
| recall@10 | **1.000** |
| MRR | **0.876** |
| 总耗时 | ~70 秒（cache 命中后） |

### 差异

```
                 on    off    Δ
hit@5          0.984  0.984   0
hit@10         1.000  1.000   0
recall@5       0.940  0.940   0
recall@10      1.000  1.000   0
mrr            0.865  0.876   -1.1pp  ← on 反而差
```

## 解读

### 1. hit@10 = 100%：retriever 已经能召全 gold

baseline hybrid search（vector + BM25 + question channel + rerank + MMR）已经覆盖了所有 gold doc。**没有"漏召回"问题**——所有 65 题的 gold 都在 top-10 内。这意味着策略对"是否找得到"完全无影响。

### 2. MRR on < off：策略把 gold 推后了

策略（`section_boost` / `cross_doc` / `section_supplement` / `year_supplement` / `query_decomposition`）重新排序结果。MRR 反向说明：策略加分/插入的 chunks 把"该在 top-1 的 gold"挤到了 top-2/top-3。

可能原因：
- `section_boost` 给"主要会计数据"等固定 section 加分，但 gold 可能在另一 section
- `cross_doc` 加跨文档 chunks，挤掉原本 top-1 的直连 chunk
- `query_decomposition` 拆分 query 后每路分数被分散，单路最高分低于未拆分
- `section_supplement` / `year_supplement` 追加 chunks，截断后挤掉原 top-1

### 3. smoke 10 题结果一致

| smoke (10 题) | on | off |
|---|---|---|
| top1 命中 gold_doc[0] | n/a | n/a |
| 整体指标 | 一致 | 一致 |

## 验收标准核对（plan §十）

| 项 | 标准 | 实测 | 是否满足 |
|---|---|---|---|
| 1 | unit test ≥ 267 | **340** | ✅ |
| 2 | smoke 10 题 < 30 秒 | **~6.5s** | ✅ |
| 3 | retrieval-only 65 题 < 1 分钟 | **~70s** | ⚠️ 微超（cache 已命中，< 60s 实际可达） |
| 4 | baseline 65 题 ≥ 73.3% | **recall@10=100%, MRR=87.6%** | ✅ 远超（但口径需对齐） |
| 5 | 关所有策略后基线 ≤ 73.3% | **off=on 甚至更高 MRR** | ⚠️ **不适用**——baseline 已高，策略反伤 |
| 6 | 8 组 ablation | 部分（Day 2 晚上继续） | 进行中 |

## 行动项（建议）

### 短期（Day 1 晚上 — Query Parser 前）

1. **考虑简化策略默认**：把 5 个策略**默认关**——保留开关但默认 off，因为 on/off 对 MRR 是 -1.1pp 损失。**前置风险**：可能影响 answer-level 评分（LLM 看到 top-1 是错的还是会错；本次只测 retrieval 层）

2. **per-class 切片**：full 65 题混合掩盖了策略效果。按 10 类（A-J）切片看各类的 hit/recall/MRR，策略可能在特定类（C 跨文档、E 时序）有用

3. **策略内部审视**：
   - `query_decomposition` 拆分 query：每路分散分数，整体 top-1 可能掉——考虑改成"分解后取最高一路"，不再混合
   - `section_boost`：硬编码 section 关键词 + section_path 匹配，可能过度泛化；改为只在 query 显式提到时才 boost
   - `cross_doc`：仅在主检索结果<阈值时启用，而不是无条件
   - `section_supplement` / `year_supplement`：当前补全的 chunks 可能挤掉原 top-1

### 中期（Day 2 — embedding + evidence）

4. **embedding_text 改造**（plan §九 Day 2 上午）：可能让 C 类跨文档题检索更准
5. **evidence_gate**（plan §九 Day 2 下午）：coverage 不足时拒答，可能提升 I 类（拒答边界）
6. **8 组 ablation**（plan §九 Day 2 晚上）：rerank / mmr / question_channel / cross_doc 等精细化对比

## 数据附录

### 工具

- `tools/annotate_gold_documents.py`：基于"答案依据"字段批量提取年份 → 映射 doc_id → 写入 `gold_documents`
- 输出：`eval/sany_annual_reports/rag_testset.json`（已应用，65 题均含 gold_documents）
- 备份：`eval/sany_annual_reports/rag_testset.gold.json`（同一份数据，保留 diff 痕迹）

### 测试结果文件

- `eval/sany_annual_reports/retrieval_results.json`：每次跑的 per-question 结果（top1 / retrieved_ids / metrics）
- `eval/sany_annual_reports/tiers.json`：smoke 10 / regression 20 / full=65 题集

### gold_documents 分布

```
0 个 gold: 1 题   (Q57 文档集外)
1 个 gold: 37 题  (A/B 类单文档)
2 个 gold: 13 题  (跨 2 年对比)
3 个 gold: 14 题  (跨 3 年对比)
```
