# 下一步计划 (2026-08-23) — Day 1 下午完成

> 接续 [docs/plans/2026-08-22-rag-decomposition.md](docs/plans/2026-08-22-rag-decomposition.md) Day 1 下午。

## 本次完成（2026-08-21，Day 1 下午）

按 plan §九 Day 1 下午 7 步执行，6/7 步代码 + 测试完成，第 7 步 baseline regression ablation 需 gold_documents 标注（Day 1 下午 P1 一起做）。

### 新增文件
- `app/core/retrieval_filter.py` — `RetrievalFilter` frozen dataclass（years/document_ids/section_names/source_types/kb_ids 五维 + `is_empty()` helper）
- `tests/unit/test_retrieval_filter.py` — 9 tests（frozen / hash / set 转换 / is_empty 等）
- `tests/unit/test_strategy_guards.py` — 12 tests（4 个 helper + `_cross_doc_extra` + pipeline._needs_decomposition 双向测试）
- `tests/unit/test_hybrid_search_filters.py` — 6 tests（filters 透传到三个通道 + 向后兼容）

### 修改文件
- `app/config.py` — 加 8 个开关：
  - `cross_doc_enabled: bool = True`
  - `section_boost_enabled: bool = True`
  - `section_supplement_enabled: bool = True`
  - `year_supplement_enabled: bool = True`
  - `query_decomposition_enabled: bool = True`
  - `evidence_gate_enabled: bool = False` (默认关，Day 2 接入)
  - `retrieval_cache_enabled: bool = True` (Day 1 上午准备，Day 1 下午 wiring)
  - `evidence_min_coverage: float = 0.7`
- `app/core/retrieval.py` —
  - `_supplement_authoritative_sections` 顶部加 `if not settings.section_supplement_enabled: return results`
  - `_supplement_missing_years` 顶部加 `if not settings.year_supplement_enabled: return results`
  - `_boost_by_section_type` 顶部加 `if not settings.section_boost_enabled: return results`
  - 抽 `async def _cross_doc_extra(...)` helper（含 `cross_doc_enabled` guard），替换 inline 36 行 block
- `app/core/pipeline.py` — `needs_decomp = settings.query_decomposition_enabled and _needs_decomposition(req.query)`
- `app/store/pgvector_store.py:398 hybrid_search` — 加 `filters: RetrievalFilter | None = None`，document_ids 字段透传到三个通道 + fallback BM25；filters 优先于旧 `document_ids` 参数

### 验证结果（fresh evidence）

```
pytest tests/unit -q
→ 340 passed, 1 failed in 10.06s

唯一失败：tests/unit/test_chunker.py::test_oversized_section_packs_on_element_boundaries
（pre-existing，git stash 后 master 8bb9d8a 同样 fail）

新增测试（Day 1 下午）：27 个全绿（9 RetrievalFilter + 12 strategy guards + 6 hybrid filters）
所有 Day 1 上午测试仍绿（45 个）
```

**Live 验证**：
| 配置 | smoke 10 题 | top1 与 default 一致 |
|---|---|---|
| 默认（全 flags on） | 10/10 返回 10 hits | n/a |
| 全 flags off | 10/10 返回 10 hits | **10/10 完全一致** |

⚠️ top1 一致说明这 5 个策略对 smoke 这 10 道简单题几乎不影响 top1——它们主要影响 C 类（跨年）和 I 类（拒答边界）这类更复杂的题（Day 1 晚上 Query Parser 接通后会显现）。retriever 没坏是底线，已达成。

## 当前状态

| 项 | 状态 |
|---|---|
| 代码（6/7 步） | ✅ 完成 |
| 27 新 unit tests | ✅ 全绿 |
| 340 unit tests 通过 | ✅（仅 1 个 pre-existing chunker 失败） |
| Smoke 全 on | ✅ 10/10 |
| Smoke 全 off | ✅ 10/10，top1 100% 一致 |
| gold_documents 标注 | ❌ 未做（plan §7.4 P1，Day 1 下午继续） |
| `_collect_results`/`_search_kb` 内部用 RetrievalFilter 替换 document_ids | ⚠️ 部分：hybrid_search 边界用了，但 `_collect_results` 内部仍用 document_ids 透传（向后兼容 OK） |

## 下一步

### 立即（你来做）

1. **commit**（建议分 2 个）：
   ```
   feat(strategy): RetrievalFilter dataclass + 7 strategy switches + 4 retrieval guards + 1 pipeline guard
   feat(hybrid_search): accept filters parameter, document_ids translation
   ```
   精确命令在 Day 1 上午的 plan doc 有模板可参考（按文件分两组）

2. **决定下一步方向**：
   - **路线 A — 继续 Day 1 下午收尾 + Day 1 晚上**（plan §九 第三段）：
     - gold_documents 标注 → 跑 regression 20 题 ablation，看各策略真实贡献
     - 接 Query Parser + Year Filter（`app/core/query_parser.py`）→ 跑 E 类 ≥ 70% 验收
   - **路线 B — 直接跳 Day 2 上午**（embedding_text 改造 + indexer 改造 + reembed_v2）

### Day 1 下午 P1 收尾（若选路线 A，约 1-2h）

3. **gold_documents 标注**：每题加 `gold_documents: ["doc_2024"]` 字段到 `eval/sany_annual_reports/rag_testset.json`
   - 共 65 题；可基于"答案依据"字段自动提取（如 "三一重工2024年年度报告" → 该年的 doc_id）
   - 简单类（A/B）通常 1 个 gold；跨年类（C/E）通常 2-3 个
4. **regression 20 题 ablation**：用 smoke 脚本 `--tier regression`，对比全 on vs 全 off 的 Recall@10/MRR
5. **跑出 baseline 报告**：写到 `docs/plans/2026-08-23-baseline-ablation.md`

### Day 1 晚上（plan §九 第三段，若路线 A）

6. 新增 `app/core/query_parser.py`（`ParsedQuery` + `parse_query()`：正则 `\d{4}` 提年份 + 关键词表提指标 + 兜底 LLM）
7. `app/core/pipeline.py` 在 retrieve 之前调 `parse_query()`，把 `parsed.filters` 透传给 retrieval
8. 删掉"靠 embedding 相似度推断年份"的代码
9. 跑 regression 20 题看 E 类（时序与追溯调整）：**验收** E 类 ≥ 70%（当前 61.1%）

### Day 2 上午

10. `app/ingestion/embedding_text.py::build_embedding_text()` 纯函数
11. `app/ingestion/indexer.py:284` 改用 build_embedding_text 喂 embed，写入 `chunk.embedding_text`
12. `app/store/db.py` 加 `embedding_version` 列
13. `tools/reembed_v2.py` 批量回填 + 用 embedding_text 重 embed

### Day 2 下午

14. `app/core/evidence.py` 暴露 `EvidenceResult` dataclass
15. `app/core/pipeline.py:300` 接入 `evidence_organizer.organize()`
16. 加 `if evidence_gate_enabled: ... return refusal_or_followup()`

### Day 2 晚上（验收日）

17. 8 组 ablation（baseline / +Year Filter / +Section Embedding / +Rerank / +Section Boost / +MMR / +Cross-doc / +Question Channel / +Evidence Gate），regression 20 题，写 `docs/plans/2026-08-23-ablation-report.md`

## 目标（验收 §十）
1. 267+ 个 unit test 通过（**当前 340 ✅**）
2. smoke 10 题 < 30 秒（含 cache 命中）— **实测 ~6.5s ✅**
3. retrieval-only eval 65 题 < 1 分钟
4. baseline 65 题 ≥ 73.3%
5. 关所有策略后 baseline ≤ 73.3%（retriever 没坏已验证 ✅；**需要 gold 标注才能给出真实分数对比**）
6. 8 组 ablation 报告

## 风险与提醒
- **当前 ablation baseline 不能给具体分数**：缺 `gold_documents` 标注，无法算 Recall/MRR；建议 Day 1 下午 P1 补上
- **top1 不变 ≠ 整体不变**：策略可能影响 top2-top10 的顺序与内容，需 Recall@5/10 指标对比；gold 标注后立即可看
- **6 个开关默认 on，evidence_gate_enabled 默认 off**：Day 2 接入 evidence 路径时再开
- **RetrievalFilter 只翻译 document_ids**：years / section_names / source_types / kb_ids 留接口等 Day 2 上午 chunks 表加 year 列
- **master 分支**：本次 + Day 1 上午累计 ~10 个未提交文件；建议 commit 前 `git checkout -b feat/rag-decomposition`

## 关键文件改动一览
| 文件 | 状态 | 说明 |
|---|---|---|
| `app/core/retrieval_filter.py` | 新增 | RetrievalFilter frozen dataclass |
| `tests/unit/test_retrieval_filter.py` | 新增 | 9 tests |
| `tests/unit/test_strategy_guards.py` | 新增 | 12 tests |
| `tests/unit/test_hybrid_search_filters.py` | 新增 | 6 tests |
| `app/config.py` | 改 1 处 | 加 8 个策略开关 |
| `app/core/retrieval.py` | 改 5 处 | 4 helper 加 guard + 抽 `_cross_doc_extra` |
| `app/core/pipeline.py` | 改 1 处 | `_needs_decomposition` 加 flag guard |
| `app/store/pgvector_store.py` | 改 1 处 | hybrid_search 接 filters |
