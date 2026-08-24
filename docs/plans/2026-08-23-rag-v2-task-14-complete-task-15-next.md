# 下一步计划 (2026-08-23) — RAG v2 Phase D Task 14 完成 + Task 15 待启

> 父计划：[2026-08-23-rag-v2-implementation-plan.md](2026-08-23-rag-v2-implementation-plan.md) · 设计 spec：[2026-08-23-embedding-migration-table-aware-design.md](2026-08-23-embedding-migration-table-aware-design.md)

## Task 14 完成 — build_retrieval_text() 纯函数

**状态**：✅ 完整 subagent-driven-development cycle 通过
- Implementer → Spec reviewer → Code reviewer → 2 轮 fix → Re-review approved

**HEAD 推进**：`7385baa` → **`b46c33d`（待确认最新 hash）**

**3 commits 串联**：

| SHA | 性质 | 内容 |
|---|---|---|
| `d0e1628a` | feat | build_retrieval_text() + 8 unit tests（TDD：test fail → impl → green） |
| `93f1b07`  | docs | spec review 找出的 10 处注释/docstring 补回（无代码改动） |
| `3745fe1`  | fix  | code review 找出的 2 Important：(a) stub-table 假阳性 guard；(b) `test_deterministic_output` 实质 a==a 改成结构不变量断言 |

**新增文件**：
- `app/ingestion/retrieval_text.py`（109 行，retrieval_text 通道归一化器）
- `tests/unit/test_retrieval_text.py`（9 个 unit test：plan 8 + code review 加 1 stub-table regression）

**测试基线守住**：
- `pytest -q`：**568 passed / 6 failed (预存) / 13 skipped**
- `ruff check` on both files：clean
- 6 failed 集合未新增（依然是预存集：`test_cross_doc_extras_reach_final_results` / 2× `test_ingestion` partial-failure / `test_questions_align_with_persisted_chunks` / `test_oversized_section_packs_on_element_boundaries` / `test_supplement_appends_missing_related_years`）

**核心契约**（spec §5.1）：
- 普通文本 chunk：`text` 原样保留，`chunk_type=None`，`tables=[]`
- Markdown 表格块：每行一条自包含句 `"{entity}：{header2}为{value2}，{header3}为{value3}。"`（全宽符号）
- 表头行保留为渲染块首行
- 缺列行（ragged）不丢数据
- 2 行 stub（仅 header + separator，0 数据行）→ `chunk_type=None`（防假阳性）
- 纯函数：仅 import `re` + `dataclasses`；无 LLM/IO/全局状态；可缓存可测试

**关键决策/偏差**：
- 计划 §Task 14 给的 8 个测试 1:1 落地；code review 加了第 9 个 stub-table regression
- 实现代码逐字符照搬 plan + 补 docstring/内联注释；spec 偏差 0
- 无副作用、未触碰任何已有文件

---

## Task 15 — indexer 接线（待启动）

**目标**：把 `build_retrieval_text()` 接入摄入管线（[plan §Task 15](2026-08-23-rag-v2-implementation-plan.md#task-15-indexer-接线embed-输入--search_text--元数据列)）

**关键变更（plan §Task 15 Step 4）**：
1. `app/ingestion/indexer.py` 顶部 import `build_retrieval_text`
2. L298 附近：embed 输入从 `c.text` 切换到归一化文本（`_embed_inputs = [rt.text for c in new_chunks]`）
3. `chunks_data.append({...})` 内：写 `embedding_text` / `embedding_version` / `chunk_type` / `table_headers` / `table_meta` 五列
4. `search_text` 两处赋值的 tokenize 源也切到归一化文本（BM25 词面对齐）
5. `app/store/pgvector_store.py` `get_chunks_by_document` 投影 dict 补这 5 键
6. **代际推进**：`app/core/cache.py` `EMBEDDING_INPUT_VERSION 1→2` · `app/config.py` `current_embedding_version 2→3`

**新单文件**：`tests/integration/test_table_aware_ingestion.py`（双表示 + 元数据列 + embedding_version 跟随 config + search_text 同源）

**通过判据**（plan §Task 15 Step 5）：
- 新集成测试 PASS
- 全量基线仍 `568 passed / 6 failed / 13 skipped`
- ruff 绿
- `import app.main` 不挂

---

## ⚠️ Phase C 教训（用户口头复盘，Task 15 必须内化为不变量）

**问题**：indexer 把 `embedding_version` 写死 1，而检索按 `current_embedding_version=2` 过滤——若不修，重摄的所有 chunk 对检索不可见（commit `a2e3958` 修复）。同源问题的另一个潜在形态：**混版本状态**。

**不变量（Task 15 集成测试必断言）**：
- **重用 chunk 的版本标签 与 新增 chunk 的版本标签必须同源 = `settings.current_embedding_version`**
- 场景：`is_reused=True` 分支（hash 命中复用）也要写 `embedding_version=settings.current_embedding_version`，**不能用历史值**
- 否则出现 `embedding_version=2旧 chunk 用 current=3 检索时部分可见` 的混版本静默错配——典型的"配置和实体不一致" bug

**Task 15 集成测试必含**：
- 模拟一个场景：先摄入文档 v2（`current=2`）；然后升级到 v3（`current=3`）；删除 → 重传 → 复用路径触发；断言所有 chunk `embedding_version == 3`，无残留 v2 chunk
- 或更轻量：fixture 里 mock 复用路径，断言 chunks_data 里 `embedding_version` 字段始终取自 `settings.current_embedding_version`，与 is_reused 无关

**为什么必做**：混版本状态在 dev 库 + 15 题快测可能漏检（快测只查检索效果、不查 corpus 完整性），只有 integrity report + 显式断言才能拦截。

---

## 当前项目状态树（增量更新）

```
Phase A 准备 → Phase B Baseline-1R → Phase C Baseline-2 → Phase D Baseline-3 → Phase E Baseline-4 → Phase F 65q
    ✅              ✅ (50.0%)            ✅ (66.7%)        ★ Task 15       待启动            待启动
                                       commit 7385baa   subagent 待派
```

## 下一步优先级

### P0 — Task 15（基线 3 唯一变量归因必做）
按 plan §Task 15 执行；`subagent-driven-development` 三阶段 review；落地后 commit 链预计 1-2 commits（含代际 bump）。

### P1 — Task 16/17
- Task 16：重摄 → Baseline-3 快测 + Gate（B 类不得下降为 Gate）→ 锁定文档
- Task 17：question channel 开闸重摄

### P2 — Task 18/19/20
- Task 18：Baseline-4 快测 + 锁定
- Task 19：65 题正式 benchmark
- Task 20：会话收尾 + 更新 TODO + 写 next-steps

## 不变量（继承自 Task 14 + Phase C 教训）

- 测试基线 `568 passed / 6 failed (= 预存集) / 13 skipped`（6 failed 集合禁止新增）
- Embedding config 已切到 `Qwen3-Embedding-8B @ 1024`（不要回 4096）
- `.env` 维持 `QUESTION_CHANNEL_ENABLED=false`（直到 Task 17 开闸）
- `chunk_questions == 0` 硬断言（直到 Task 17）
- **`embedding_version` 必须单一来源 = `settings.current_embedding_version`**（Phase C 教训，Task 15 必断言）

## 关联文档

- 实施计划: [2026-08-23-rag-v2-implementation-plan.md](2026-08-23-rag-v2-implementation-plan.md)
- 设计 spec: [2026-08-23-embedding-migration-table-aware-design.md](2026-08-23-embedding-migration-table-aware-design.md)
- Baseline-2 锁定: [2026-08-23-baseline-2-locked.md](2026-08-23-baseline-2-locked.md)（含 Phase C 踩坑记录）
- Phase 2/3 历史: [2026-08-23-next-steps.md](2026-08-23-next-steps.md)（Evaluator v1 收口版，已被本档覆盖 RAG v2 部分）