> 状态: 进行中（分支 fix/audit-followups）

# 全栈审查遗留修复（audit-followups）实施计划

> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪。原则：**每个修复先落测试**（新纯逻辑 → `tests/unit` 离线单测；DB 行为 → `tests/integration` ragent_test 库），再改实现；全部完成后全量回归。

**Goal:** 修复 2026-08-04 全栈代码审查发现的 16 项问题：3 项静默失效的实质 bug（P0）、5 项正确性/安全问题（P1）、8 项性能与工程整洁（P2）。

**Architecture:** 改动集中在后端 `app/` 与前端 `ChatView.vue`。不改现有表结构（沿用 init_db 幂等 ALTER 的既有约定，本计划无需新列）；不引入新后端依赖；前端零新依赖（HTML 转义 + URL 协议过滤，不上 DOMPurify）。所有 DB 侧修复用 `tests/integration`（fake embedding 层）锁定，纯逻辑修复用 `tests/unit` 锁定。

**Tech Stack:** pytest（unit 离线 / integration ragent_test）、FastAPI、SQLAlchemy、Vue 3 + marked。

---

## Context

2026-08-04 通读全库（后端 ~8.5k 行 + 前端 ~1.6k 行）后的发现清单。两个 P0 都是「稳定 chunk id」（commit `4c3a918`）改造的连带损伤：

1. **增量更新丢失复用 chunk 的问题向量**——`replace_chunks` 全删全插，`chunk_questions` 外键 CASCADE 把复用 chunk 的问题行删光，而 `question_source` 只为新 chunk 重建 → 每次增量上传都在损耗 question 通道。`indexer.py:195` 注释宣称的设计意图（"复用 chunk 的问题向量行不再孤儿化"）被 CASCADE 击穿。
2. **上下文扩展整体失效**——`get_neighbor_chunks` 用 `^(.+)_(\d+)$` 解析 chunk 尾部序号，但 chunk id 已改为 `{doc_id}_{hash[:10]}`，十六进制尾巴几乎永不匹配 → `pipeline.py` 的 ±2 邻居扩展静默拿到空结果。
3. **前端 XSS**——`ChatView.vue` 用 `v-html + marked.parse` 渲染且 marked 默认透传原始 HTML；文档内容可经提示词注入让 LLM 原样引用 `<img onerror=...>`，token 在 localStorage → 会话劫持。

## Design

### P0-1 增量更新保留复用 chunk 的问题向量

`replace_chunks` 从「全删全插」改为**差量 upsert**：

1. 取文档现有 chunk_id 集合；
2. `DELETE` 不在新集合中的 chunk（CASCADE 只清理真正消失的 chunk 的问题行）；
3. 现有 chunk 逐行 `UPDATE` 全字段（含 visibility/allowed_roles——重上传可能换 KB 可见性），并**刷新 created_at = base_ts + i 微秒**（顺序语义见 P0-2）；
4. 新 chunk `INSERT`。

复用 chunk 的行不被删除 → 外键上的 `chunk_questions` 存活，无需重 embed。`delete_orphan_chunk_questions` 保留为兜底，但修掉 LIKE 通配符问题（`_` 在 LIKE 中是单字符通配——对 `doc_id + "_%"` 做转义）。

### P0-2 上下文扩展适配 hash chunk id

不再解析 chunk_id 里的序号（已不存在）。改为：

- `get_neighbor_chunks(anchors, expand_n)`，`anchors` 为 `[(document_id, chunk_id), ...]`（pipeline 手里就有 document_id，无需从 id 反解）；
- 每文档按 `created_at` 排序取全文 chunk 序列，Python 侧定位锚点切片 ±N（P0-1 保证增量更新后 created_at 仍完整编码逻辑顺序；`Chunk.id` 不行——后插入的 chunk id 更大但逻辑位置可能在中间）；
- `pipeline.py` 调用点改为传 `(document_id, chunk_id)` 对，并经 `asyncio.to_thread` 调用（见 P2-9）。

### P0-3 前端 XSS 消毒（零新依赖）

新增 `frontend/src/util/render.ts`：`renderMarkdown(text)` = 先 HTML 转义（`& < >`）杀死一切原始 HTML 注入，再 `marked.parse`；渲染后再过滤 `javascript:`/`vbscript:` 协议 href/src（防 `[x](javascript:...)` 链接点击型 XSS，保留 `data:image`）。`ChatView.vue` 两处 `v-html`（历史消息 + 流式）统一走它；legacy `<think>` 标签剥离保持在转义**之前**。

### P1-4 意图路由归一 + 守卫

- `intent.py` 提取纯函数 `_normalize_matches(raw, kb_ids, kb_names)`：非 dict / 缺键 / score 非数值 → 丢弃；`kb_id` 是名称 → 经 `kb_names` 反查 id；查不到 → 丢弃并留痕。`classify` 内 `IntentMatch` 构造改走该函数，KeyError 崩溃面消失；
- `pipeline.py::_retrieve_one` 的 `classify` 调用包 try/except 兜底为 `intent=None`。

### P1-5 诊断接口路径穿越

`api/diagnostics.py`：`diag_detail`（`:path` 转换器允许斜杠）与 `diag_chunk_doc` 的入参统一过 `_SAFE_ID_RE = ^[0-9A-Za-z][0-9A-Za-z-]{0,63}$`，不匹配 → 404。

### P1-6 熔断降级文案流式可见

`pipeline.execute`：`except CircuitOpenError` 分支立即把降级文案作为 `token` 事件 yield（此前只写库不流式，用户当轮看到空白）；正常收尾路径用该文案兜底 `answer_text` 持久化。

### P1-7 个人工作空间隐私 + owner ACL

- 注册时创建的工作空间 KB 由 `public` 改 `restricted`；
- 四个检索 SQL（`search` / `bm25_search` / `question_vector_search` / `get_chunks_by_documents_bulk`）增加 **owner 旁路**：签名加 `user_id: str | None`，WHERE 增加 `OR (:user_id <> '' AND EXISTS (SELECT 1 FROM documents d WHERE d.document_id = chunks.document_id AND d.owner_id = :user_id))`；
- `hybrid_search` → `retrieval._search_kb/_collect_results/RetrievalEngine.retrieve` → `pipeline` 与 `cross_doc retriever` 全链路透传 user_id。
- 不做存量数据迁移（旧 public 工作空间维持现状，plan 里注明）。

### P1-8 SSE 文档进度：按用户过滤 + 满队列丢旧

- 订阅者结构改 `{"queue", "user_id"}`；`emit_doc_progress` 按事件里的 `user_id` 定向投递，`QueueFull` 时丢一条最旧再投（终态事件不再被静默吞掉）；
- indexer 的 emit 全部携带 `user_id`，且中间进度按 5% 节流（模块级 `{doc_id: 上次百分比桶}`，终态清理）。

### P2 批次

| # | 修复 | 方案 |
|---|------|------|
| 9 | 事件循环阻塞 | `pipeline` 的 `_resolve_doc_map` / `get_neighbor_chunks` 走 `asyncio.to_thread`；`middleware/auth.get_current_user/get_optional_user` 的同步 DB 解析包 `to_thread`；`documents._resolve_sse_user` 同样处理 |
| 10 | BM25 tsquery 注入/语法错误 | `tokenize` 输出前剥离 tsquery 运算符字符（`|&!():<>'"\` 等，仅保留 `\w` 与连字符），查询含 `C++` 之类不再让 to_tsquery 抛错 |
| 11 | embedding 单批无分片 | `embed_with_fallback` 按 32 条分片逐批尝试 batch，失败批退化为该批逐条（不再整单退化） |
| 12 | 进度广播风暴 | 并入 P1-8 的 5% 节流 |
| 13 | 死代码清理 | 删 `chunks_max_tokens` 配置；`whitelist`/`false-positive` 共享同一处置函数；`diag/chunks` 与 `admin/chunks` 合并查询实现；`embedding.py` 重复 `import time as _time` 删除；`doc_relation` 冗余 `getattr` 直读 settings |
| 14 | chunker 原子块 + 重叠 | 超长 section 改**元素级装箱**（atomic 块不被拦腰切断，单元素超限才文本硬切）；`_hard_split` 增加 64 字符重叠窗口（README 宣称的行为落地） |
| 15 | LIKE 通配符 | 并入 P0-1（`delete_orphan_chunk_questions` 转义） |
| 16 | get_messages 排序 | `created_at.asc()` → `id.asc()`（与 memory 模块的单调序约定对齐） |

## Files to change

| 变更 | 路径 |
|---|---|
| Modify | `app/ingestion/indexer.py`、`app/store/pgvector_store.py`、`app/core/pipeline.py`、`app/core/retrieval.py`、`app/core/doc_relation.py`、`app/core/intent.py`、`app/llm/embedding.py`、`app/api/documents.py`、`app/api/diagnostics.py`、`app/api/admin.py`、`app/api/chat.py`、`app/api/auth.py`、`app/middleware/auth.py`、`app/config.py`、`app/ingestion/chunker.py`、`frontend/src/views/ChatView.vue`、`README.md`（重叠窗口表述核对） |
| Create | `frontend/src/util/render.ts`、`tests/unit/test_intent_guard.py`、`tests/unit/test_diag_path_guard.py`、`tests/unit/test_sse_delivery.py`、`tests/unit/test_embed_batching.py`、`tests/unit/test_tsquery_sanitize.py` |
| Modify | `tests/integration/test_ingestion.py`（问题向量保留 + 邻居扩展 + 特殊字符检索）、`tests/integration/test_security_api.py` 或新文件（owner ACL / 工作空间隔离 / diag 穿越 API 级）、`tests/integration/test_retrieval_e2e.py`（熔断降级流式）、`tests/unit/test_chunker.py`（装箱 + 重叠）、`docs/plans/README.md` |

## Reused existing utilities

`tests/integration/conftest.py::fake_llm_stack / ingest_docs / _precreate_document_row 模式`、`tests/unit` 纯函数直测模式、`TagStreamParser` 的离线单测先例、init_db 幂等迁移约定。

---

## Tasks

### Task 1: P0-1 + P0-2（摄入/检索正确性核心）

- [ ] **Step 1**: integration 测试先行：`test_incremental_update_preserves_reused_questions`（首摄 → 记录复用 chunk 的问题行数 → 追加小节增量更新 → 断言存活 chunk 的问题行仍在、新 chunk 问题行新增）、`test_neighbor_expansion_returns_context`（真实锚点取 ±2 邻居，断言 before/after 非空且来自相邻块）。
- [ ] **Step 2**: `replace_chunks` 差量化 + `created_at` 顺序刷新 + `delete_orphan_chunk_questions` LIKE 转义。
- [ ] **Step 3**: `get_neighbor_chunks` 重写（anchors 对 + created_at 排序 + Python 切片），pipeline 调用点适配。
- [ ] **Step 4**: 跑 `pytest tests/integration/test_ingestion.py tests/integration/test_cross_doc.py tests/integration/test_retrieval_e2e.py -q`，全绿后 commit。

### Task 2: P0-3 前端 XSS

- [ ] **Step 1**: 建 `frontend/src/util/render.ts`（转义 → marked → 协议过滤），ChatView 两处 v-html 接入。
- [ ] **Step 2**: `npm run build`（vue-tsc 类型检查）通过，commit。

### Task 3: P1-4 意图归一 + 守卫

- [ ] **Step 1**: 先写 `tests/unit/test_intent_guard.py`（缺键/坏类型/名称替身/非 dict 条目 → 不抛且归一正确）。
- [ ] **Step 2**: `_normalize_matches` 落地 + pipeline classify 兜底，单测 + 回归绿，commit。

### Task 4: P1-5/7 安全批（路径穿越 + 工作空间 ACL）

- [ ] **Step 1**: `tests/unit/test_diag_path_guard.py`（合法/非法 id 判定）。
- [ ] **Step 2**: integration：双用户场景——A 的 restricted 工作空间文档，B 检索不得命中、A 本人可命中、admin 可命中；`GET /api/v1/diag/detail/../../...` 返回 404。
- [ ] **Step 3**: 实现 SQL owner 旁路（四处）+ 全链路 user_id 透传 + register 默认 restricted + diag id 守卫，测试绿，commit。

### Task 5: P1-6/8 + P2-9（pipeline/documents 行为批）

- [ ] **Step 1**: integration：monkeypatch `chat_stream` 抛 `CircuitOpenError`，断言 SSE 含降级文案 token 且消息入库。
- [ ] **Step 2**: `tests/unit/test_sse_delivery.py`：按 user 过滤 + QueueFull 丢旧不丢新（asyncio 直测）。
- [ ] **Step 3**: 实现熔断文案流式、SSE 过滤/丢旧/节流、三处 to_thread，测试绿，commit。

### Task 6: P2-10/11/14（检索/摄入质量批）

- [ ] **Step 1**: 先写单测：`test_tsquery_sanitize.py`（`tokenize("C++")` 无运算符；integration 补 `bm25_search("C++")` 不抛）、`test_embed_batching.py`（70 条 → 32/32/6 分片；单批失败只退化该批）、`test_chunker.py` 扩展（超长 section 装箱不切断 atomic；hard split 带重叠）。
- [ ] **Step 2**: 实现三处，测试绿，commit。

### Task 7: P2-13/16 清理批 + 回归

- [ ] **Step 1**: 死代码/死配置清理、`get_messages` 改 id 序。
- [ ] **Step 2**: 全量 `pytest -q` + `python -c "import app.main"` + 前端 build，commit。

### Task 8: 收尾

- [ ] **Step 1**: plan 状态转已完成，README 索引更新，commit。

## Verification

| 验证项 | 命令 | 期望 |
|---|---|---|
| 全量套件 | `D:/miniConda/envs/rag/python.exe -m pytest -q` | ≥112 passed + 新增用例全绿（live 仍 skip） |
| 离线单测 | `... -m pytest tests/unit -q` | 全绿（新增 ~25 例） |
| import 链 | `D:/miniConda/envs/rag/python.exe -c "import app.main"` | 无异常 |
| 前端 | `cd frontend && npm run build` | vue-tsc + vite 通过 |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| 存量 public 工作空间数据迁移 | 只改新增默认值；迁移需产品决策，另行讨论 |
| 引入 DOMPurify | 零依赖方案已覆盖注入面；避免新前端依赖 |
| chunks 表加 position 列 | created_at 顺序刷新已满足邻居扩展，不动 schema |
| live_llm 回归 | 本批修复全有离线覆盖；live 成本高，按需手动 |
| chunker 全局重叠窗口 | 只在 hard-split 路径加 64 字符重叠；section 边界本身是语义边界，跨 section 重叠会改变全部 chunk hash 引发全量重 embed |
