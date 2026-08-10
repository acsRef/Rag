# 设计审查遗留修复（design-review-fixes）实施计划

> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪。原则：**每个修复先落测试**（新纯逻辑 → `tests/unit` 离线单测；DB 行为 → `tests/integration` ragent_test 库），再改实现；全部完成后全量回归。

**Goal:** 修复 2026-08-06 通读全库（后端 ~8.85k 行 + 前端 ~1.6k 行）后的设计审查发现项：6 项正确性/功能缺口（P0）、5 项性能（P1）、5 项架构整洁（P2）、2 项前端/安全低成本（P3）、1 项文档修正（P4）。

**Architecture:** 改动集中在后端 `app/`、前端 `ChatView.vue`/`tsconfig` 与文档。不改现有表结构（沿用 init_db 幂等 ALTER 约定，本计划无需新列）；不引入新后端依赖；前端零新依赖。所有 DB 侧修复用 `tests/integration`（fake embedding 层）锁定，纯逻辑修复用 `tests/unit` 锁定。

**Tech Stack:** pytest（unit 离线 / integration ragent_test）、FastAPI、SQLAlchemy、Vue 3 + marked。

---

## Context

2026-08-06 通读全库后的发现清单，按严重度分组：

**P0（正确性 / 功能缺口）**
1. `diagnostics_enabled` 配置是死的——`api/chat.py:23` 无条件创建 `DiagContext` 传给 pipeline，`pipeline.py:132` 的 `if ctx is None and settings.diagnostics_enabled` 对 chat 路径永不生效，遥测文件每次都写。
2. 前端错误双气泡——后端 PII 拒答/降级先发 `error` 再发 `done`；`ChatView.vue` 的 `onDone` 无条件 push 一条 `streamingContent`（空）消息。
3. 删除文档/KB 泄漏入边关系——`doc_relations.target_doc` 边需手动清理（`doc_relation.py:16` 自注），但 `delete_document` / `delete_kb` 都没调 `delete_doc_relations_by_doc_id`（该函数从未被调用）。
4. thinking 内容丢失——`messages.thinking_content` 已存，但 `get_messages` API 不返回，历史重载只解析 legacy `<think>` 标签。
5. 索引器孤儿 future——`indexer.py:185-196` `_meta_fut.result()` 抛异常时 `_embed_fut` 不被 await/cancel，embedding 线程悬空。
6. `content_hash` 在 PII 脱敏前计算——hash 基于原始文本、落库内容已 mask；PII 规则变更后重传被 hash 跳过。

**P1（性能 / 扩展性）**
7. 检索 N+1——`_collect_results` 逐 KB 串行调 `hybrid_search`（每 KB 最多 4 次顺序 DB 查询）。修复：`asyncio.gather` 并行各 KB（保留 per-KB top_k 语义，去重需并发安全）。
8. 跨文档关系 O(N)/次摄入——`update_for_document` 每次全量 load 所有文档实体并重算 global DF。修复：global DF 走 SQL 聚合，candidate 收敛有界。
9. `get_neighbor_chunks` 瘦查询（低优先，改为只取 chunk_id/text 两列）。
10. 鉴权每请求 3 次 DB 查询无缓存——加进程内 TTL 缓存（60s）。
11. `documents.py` async handler 内同步 DB 阻塞事件循环——统一 `to_thread` / 改 `def`。

**P2（架构 / 整洁）**
12. `@app.on_event("startup")` + `asyncio.get_event_loop()` 废弃——改 lifespan。
13. 停用词表两处重复（`pgvector_store._STOP_WORDS` vs `doc_relation._STOPWORDS`）——合并共享模块。
14. 一次性迁移工具 `clean_all_table_chunks` 躺在 store 层——外移 `tools/`。
15. `_needs_decomposition` 正则误报（`它` 命中 `其它`）——负向后视收紧。
16. health 探针 DB 无超时——engine 加 connect_timeout。

**P3（前端 / 安全低成本）**
17. 前端 tsconfig 缺 `noEmit`——构建产物 `.js/.vue.js` 污染 `src/`。
18. `_admin_role_id` 缓存永不失效 + 登录限流多 worker 限制注释。

**P4（文档）**
19. AGENTS.md 写死「本仓库没有 pytest/tests 目录」——与 193 个测试的实际不符，修正。

---

## Design

### P0-1 diagnostics flag 生效

`api/chat.py::stream_chat` 改条件创建：
```python
ctx = DiagContext(query=req.query) if settings.diagnostics_enabled else None
```
保留 `pipeline.py:132` 的兜底。落 unit：`diagnostics_enabled=False` 时 chat 流不写诊断文件。

### P0-2 前端错误双气泡

`ChatView.vue` 加 `streamError` ref；`onError` 回调置位并清 `streamingContent`；`onDone` 仅在 `streamingContent || thinkText || (未发生 error 且已有内容)` 时 push 助手气泡，否则跳过。`abortStream`/切换会话时复位。

### P0-3 删除清入边关系

`documents.py::delete_document` 与 `kb.py::delete_kb`：对每个待删 doc_id 调 `pgvector_store.delete_doc_relations_by_doc_id(doc_id)`（清理 source+target 两侧）。落 integration：删除后 `doc_relations` 无任何行指向该 doc。

### P0-4 thinking 回传

`api/chat.py::get_messages` 返回值加 `thinking_content`；`ChatView.vue::loadMessages` 优先读 `m.thinking_content`，缺则回退解析内联标签。

### P0-5 索引器未来孤儿

`indexer.py` 用 `concurrent.futures.wait([_meta_fut, _embed_fut], FIRST_COMPLETED)` + try/finally：任一失败时取消另一个并 `consume` 其 exception，避免悬空线程；正常路径按序取结果。

### P0-6 content_hash 后置 PII

`indexer.py` 把 `doc_hash = _content_hash(text)` 移到 PII `mask_text` 之后，保证「存储内容变 → hash 变 → 重索引」。

### P1-7 检索并行化

`retrieval.py`：`_collect_results` 改 async，内部对每个 kb 起 `asyncio.to_thread(_search_kb, ...)` 并用 `asyncio.gather` 并行；`seen_ids`/`results` 的写入在同一事件循环内由 gather 顺序收集后统一处理（避免线程竞态：gather 返回各自结果列表，主协程归并去重）。两处调用点改 `await _collect_results(...)`。

### P1-8 跨文档 DF 走 SQL 聚合 + candidate 收敛

- 新增 `pgvector_store.get_global_df()`：SQL `SELECT entity, COUNT(DISTINCT document_id) FROM doc_entities GROUP BY entity` 返回 `(df_dict, total_docs)`。
- `TfidfFeatureExtractor.refresh_global_stats()` 增加 db 参数：从 DB 聚合载入，不再把全量实体拖进内存。
- `DocRelationBuilder.update_for_document`：candidate 收敛为「与本文档实体有重叠的文档」，用 `get_doc_ids_with_any_entity(terms)`（SQL `WHERE entity = ANY(:terms)` 去重），不再 `get_all_doc_ids_with_entities() + get_doc_entities_bulk(all)`。语料超大时行为保持有界。

### P1-10 auth TTL 缓存

`middleware/auth.py`：`_build_user_dict` 的 roles/permissions 查询结果按 user_id 缓存 60s（进程内 dict + 时间戳）；`auth_store` 角色/权限变更后调用 `invalidate_user_cache(user_id)`（管理接口改角色处接线）。落 unit：缓存生效 + 失效后可见新权限。

### P1-11 async handler 补 to_thread

`documents.py`：`upload_document` 内 `_get_kb_visibility`/`_resolve_document_id`/`_upsert_processing_document` 包 `asyncio.to_thread`；`list_documents`/`get_document` 改同步 `def`（FastAPI 线程池执行）。

### P2-12 lifespan

`main.py` 改 `@asynccontextmanager` lifespan，`set_main_loop(asyncio.get_running_loop())`，移除 `@app.on_event`。

### P2-13 停用词合并

新建 `app/core/stopwords.py` 单一 `STOP_WORDS` 常量（取两表并集，保留 query 侧语义）；`pgvector_store` 与 `doc_relation` 引用同一常量。

### P2-14 clean_all_table_chunks 外移

迁到 `tools/clean_all_table_chunks.py`（模块 + `__main__` 入口），`pgvector_store` 删除该方法（保留 `_clean_tables_in_text` 供工具使用，或一并外移）。

### P2-15 改写触发加固

`pipeline.py::_needs_decomposition` 规则 4 的 `它` 用负向后视排除 `其它|其他`（`(?<!其)它` 后仍需排除后续），直接改为 `(其它|其他|它们|它)` 词表化处理；中英文规则归一并注释魔数。

### P2-16 health 超时

`db.py` engine `create_engine(..., connect_args={"connect_timeout": 2})`（PG 驱动 connect timeout；pool_pre_ping 已开）。

### P3-17 tsconfig noEmit

`frontend/tsconfig.app.json` 加 `"noEmit": true`；删除 `src/**/*.js` 与 `*.vue.js` 残留产物（gitignored，可删）。

### P3-18 _admin_role_id 失效 + 限流注释

`middleware/auth.py::_get_admin_role_id` 加 TTL（如 300s）或提供 `invalidate_admin_role()` 在角色变更处调用；`api/auth.py` 登录限流加「多 worker 需共享存储 + 受信代理才可用 X-Forwarded-For」注释。

### P4-19 AGENTS.md 修正

改写「本仓库没有 pytest/tests 目录」段落：说明仓库含 `tests/`，pytest 按 `unit`/`integration`/`live_llm` 标记组织，补常用命令。

---

## Validation

- 每项先落测试：纯逻辑 → `tests/unit`；DB/并行 → `tests/integration`；不新增联网 live 用例。
- 收尾全量 `pytest`（保持全绿）+ `npm run build`（vue-tsc 类型检查）+ `python -m app.main` import 验证。
