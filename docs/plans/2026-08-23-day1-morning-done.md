# 下一步计划 (2026-08-23)

> 接续 `docs/plans/2026-08-22-rag-decomposition.md` Day 1 上午的收尾与 Day 1 下午启动。

## 本次完成（2026-08-21，Day 1 上午）

按 plan §九 Day 1 上午 6 步执行，前 5 步代码 + 测试全部落地，第 6 步 smoke 跑通需 backend 启动，留给你。

### 新增文件
- `eval/metrics.py` — 纯函数：Recall@5/10、MRR、Hit@5/10、`compute_all()`
- `app/core/cache.py` — `EmbeddingCache` + `RetrievalCache`（OrderedDict LRU，sha256 key，模块级 singleton）
- `eval/retrieval_eval.py` — CLI：调 `/api/v1/retrieve`，不调 LLM，支持 `--tier smoke|regression|full`
- `eval/sany_annual_reports/tiers.json` — 三档 tier 题集（smoke 10 / regression 20 / full=all）
- `tests/unit/test_metrics.py` — 19 tests
- `tests/unit/test_cache.py` — 19 tests
- `tests/unit/test_embedding_cache_integration.py` — 7 tests（mock client 锁 cache + rate limiter + 双路径）

### 修改文件
- `app/config.py` — 加 `embedding_cache_enabled: bool = True`（env: `EMBEDDING_CACHE_ENABLED`）
- `app/llm/embedding.py` — `embed()` 与 `embed_single_chunk()` 接入 EmbeddingCache；
  - cache hit 在 rate limiter / 熔断检查 **之前**（节省 token + 不计熔断）
  - settings 关时 get/set 双旁路

### 验证结果（fresh evidence）
```
pytest tests/unit -q
→ 313 passed, 1 failed in 16.48s

唯一失败：tests/unit/test_chunker.py::test_oversized_section_packs_on_element_boundaries
→ git stash 后 master 8bb9d8a 同样 fail（pre-existing，与本次改动无关）

新增测试：45 个全绿（19 metrics + 19 cache + 7 integration）
import chain OK（app.main, eval.retrieval_eval, eval.metrics, app.core.cache 全部可导入）
settings.embedding_cache_enabled = True（默认开）
```

## 当前状态

| 项 | 状态 |
|---|---|
| 代码（5/6 步） | ✅ 完成 |
| Unit tests（45 新） | ✅ 全绿 |
| 现有 313 unit tests | ✅ 全绿（仅 1 个 pre-existing chunker 失败） |
| Step 6 smoke 10 题实测 | ⏸️ 需 backend 跑起来 + 你手动执行 |
| Tier 字段 / gold_documents 已加到 testset？ | ❌ 未做（plan §7.4，归 Day 1 下午 P1） |

## 下一步（按优先级）

### 立即（你来做）
1. **跑 baseline smoke 验收**：
   ```bash
   docker compose up -d
   D:/miniConda/envs/rag/python.exe -m app.main     # 终端 1
   D:/miniConda/envs/rag/python.exe eval/retrieval_eval.py --tier smoke   # 终端 2
   ```
   期望：10 题都返回非空 items，retrieved_count ≥ 1，无 500 错误。结果写到 `eval/sany_annual_reports/retrieval_results.json`。
   - 若任一题空结果：说明 retriever 被 cache 或别的改动破坏了，回到 `app/core/retrieval.py` 排查
   - 全程耗时应 < 30s（cache 让 query embedding 走捷径）

2. **决定 commit 节奏**：现在 master 上有 6 个新文件 + 2 处修改。建议分两个 commit：
   - commit 1：`feat: eval metrics + retrieval-only eval + tier config`（不动业务代码）
   - commit 2：`feat(cache): in-memory LRU embedding cache + config switch`
   - 命令：
     ```bash
     git add eval/metrics.py eval/retrieval_eval.py eval/sany_annual_reports/tiers.json \
             tests/unit/test_metrics.py
     git commit -m "feat: eval metrics + retrieval-only eval + tier config"
     git add app/core/cache.py app/config.py app/llm/embedding.py tests/unit/test_cache.py \
             tests/unit/test_embedding_cache_integration.py
     git commit -m "feat(cache): in-memory LRU embedding cache + config switch"
     ```

### Day 1 下午（plan §九 第二段，2-3h）
3. **策略开关 + RetrievalFilter 接 baseline**（plan §四 + §二）：
   - `app/config.py` 加 7 个开关：`cross_doc_enabled`、`section_boost_enabled`、`section_supplement_enabled`、`year_supplement_enabled`、`query_decomposition_enabled`、`evidence_gate_enabled`、`retrieval_cache_enabled`
   - `app/core/retrieval.py` 在 line 593/617-642/782/788 4 处无条件调用加 `if settings.xxx_enabled:` guard
   - `app/core/pipeline.py` 在 `_needs_decomposition` 前加 `query_decomposition_enabled` guard
   - 新增 `app/core/retrieval_filter.py`（dataclass：years/document_ids/section_names/source_types/kb_ids）
   - `app/store/pgvector_store.py:398 hybrid_search` 接收 `filters: RetrievalFilter`，翻译成 SQL WHERE
   - 把 `app/core/retrieval.py` 里 `if year == ...` 散装判断全删，换 RetrievalFilter 注入
4. **跑 baseline regression**（20 题关闭所有策略），跟 73.3% baseline 对比，**验收** baseline ≤ 73.3%

### Day 1 晚上（plan §九 第三段，1-2h）
5. **Query Parser + Year Filter**（plan §三）：
   - 新增 `app/core/query_parser.py`（`ParsedQuery` + `parse_query()`：正则 `\d{4}` 提年份 + 关键词表提指标 + 兜底 LLM）
   - `app/core/pipeline.py` 在 retrieve 之前调 `parse_query()`，把 `parsed.filters` 透传给 retrieval
   - 删掉"靠 embedding 相似度推断年份"的代码（`_supplement_missing_years` 等）
6. **跑 regression 20 题看 E 类（时序与追溯调整）**：**验收** E 类 ≥ 70%（当前 61.1%）

### Day 2 上午（embedding_text + indexer 改造）
7. `app/ingestion/embedding_text.py::build_embedding_text()` 纯函数
8. `app/ingestion/indexer.py:284` 改用 build_embedding_text 喂 embed，写入 `chunk.embedding_text`
9. `app/store/db.py` 加 `embedding_version` 列（init_db 已有 `ADD COLUMN IF NOT EXISTS` 模式）
10. `tools/reembed_v2.py` 批量回填 + 用 embedding_text 重 embed

### Day 2 下午
11. `app/core/evidence.py` 暴露 `EvidenceResult` dataclass
12. `app/core/pipeline.py:300` 接入 `evidence_organizer.organize()`
13. 加 `if evidence_gate_enabled: ... return refusal_or_followup()`

### Day 2 晚上（验收日）
14. 8 组 ablation（baseline / +Year Filter / +Section Embedding / +Rerank / +Section Boost / +MMR / +Cross-doc / +Question Channel / +Evidence Gate），regression 20 题，写 `docs/plans/2026-08-23-ablation-report.md`

## 目标（验收 §十）
1. 267+ 个 unit test 通过（当前 313 ✅）
2. smoke 10 题 < 30 秒（含 cache 命中）
3. retrieval-only eval 65 题 < 1 分钟
4. baseline 65 题 ≥ 73.3%
5. 关所有策略后 baseline ≤ 73.3%（证明策略真有效）
6. 8 组 ablation 报告

## 风险与提醒
- **master 分支**：本次改动还在 master 上，建议下午 commit 前开 feature branch（`git checkout -b feat/rag-decomposition`）以免影响后续切换
- **embedding cache 是进程内**：多 worker 部署需要换 Redis（plan §六.6.1 已记）；Day 1 单进程足够
- **`/api/v1/retrieve` 不调 chat pipeline**：所以 ablation 时它不能反映 MMR / cross_doc / rerank 的影响——只测 baseline + section embedding 那种纯检索改动才用
- **RetrievalCache class 已写但暂未集成**：Day 1 上午只为稳定 key 契约；plan §六.6.2 推迟到 Day 1 下午 / Day 2

## 关键文件改动一览
| 文件 | 状态 | 说明 |
|---|---|---|
| `eval/metrics.py` | 新增 | 5 个指标纯函数 |
| `app/core/cache.py` | 新增 | EmbeddingCache + RetrievalCache LRU |
| `eval/retrieval_eval.py` | 新增 | CLI 跑 retrieval-only eval |
| `eval/sany_annual_reports/tiers.json` | 新增 | smoke/regression/full 配置 |
| `tests/unit/test_metrics.py` | 新增 | 19 tests |
| `tests/unit/test_cache.py` | 新增 | 19 tests |
| `tests/unit/test_embedding_cache_integration.py` | 新增 | 7 tests |
| `app/config.py` | 改 1 处 | `embedding_cache_enabled: bool = True` |
| `app/llm/embedding.py` | 改 2 处 | embed() + embed_single_chunk() 接 cache |
