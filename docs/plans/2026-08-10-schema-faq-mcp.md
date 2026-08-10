# 2026-08-10 Schema FAQ MCP：FAQ 知识库 + ingest/search_faq 工具

> 状态: 已完成（commit `94c4930`；后续 2026-08-10 `e2ff172` 补跨进程 token 缓存，见 mcp_server/token_cache.py）

## Context

ReportAgent（SQL Agent）的业务口径 RAG 要从 ragent-py 走 MCP 查询。用户需求：FAQ 知识库灌进 ragent-py 的 RAG，ReportAgent 后端用 stdio MCP client 连本服务的 `search_faq` 工具检索，注入 SQL 生成 prompt（目标 SQL 准确率 70% → 80%）。

现状（查证属实）：本服务已有 `mcp_server/` 数据字典 MCP 桥（`ingest_table_schemas` / `upsert_api_dictionary` / `search_dictionary` / `list_dictionary_docs`，stdio 传输，`mcp>=2.0.0` 构造期绑定 `on_list_tools`/`on_call_tool`，`cmd_*` 模块级函数便于单测）。但**没有 FAQ 知识库**，也没有面向 SQL Agent 的 FAQ 检索工具。

## Design

在 `mcp_server/` 内镜像既有字典桥模式，新增独立「FAQ」知识库（与「数据字典」KB 隔离，env `FAQ_KB_NAME` 默认 `FAQ`），三个 MCP 工具：`ingest_faq` / `search_faq` / `list_faq_docs`。

### 1. 文档渲染（`mcp_server/render.py`）

- `faq_filename(faq_id) -> str`：`faq-<id>.md`——确定性文件名即幂等键（同名复用 document_id，增量摄入既有能力白拿）。
- `render_faq_doc(*, question, keywords, tables, sql, note) -> str`：Markdown 单文档，标题=问题（检索主命中面），关键词/涉及表一行，`示例 SQL` 代码块，`要点` 段。FAQ 条目短（SQL ~5 行 + note 1 行），默认 chunk_size=512 下单 chunk，检索命中即得【问题+SQL+要点】全文。

### 2. MCP 工具（`mcp_server/server.py`）

| 工具 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `ingest_faq` | `faqs: [{id?, question, keywords[], tables[], sql, note}]` | 逐条 `render_faq_doc` → ensure FAQ KB → `upload_document`（确定性文件名）→ `wait_indexed` | 每条 `{id, filename, document_id, status, chunk_count, error}` |
| `search_faq` | `query`、`top_k`(默认 5) | `client.retrieve(query, [faq_kb], top_k)` | `{matches: [{chunk_id, document_id, text, title, score}], degraded}`；无匹配 `FAQ 无匹配：<query>` |
| `list_faq_docs` | 无 | `client.list_documents(faq_kb)` | 文档清单 + 摄入状态 |

- `_faq_kb_name()` 读 `FAQ_KB_NAME`（默认 `FAQ`）。
- 复用 `_client_context()` / `_client_error()` / `RagentClient`（`ensure_kb` / `upload_document` / `wait_indexed` / `retrieve` / `list_documents`）——**零新增 HTTP 面**，全部走既有 `/api/v1/*`。
- 单词典 `cmd_*` 同名 stub 模式：`cmd_ingest_faq` / `cmd_search_faq` / `cmd_list_faq_docs`，`_DISPATCH` + `handle_list_tools` 注册。
- 错误路径与字典桥对齐：缺 `faqs`/`query` → 中文缺参文案；`top_k` 非整数 → 提示；`RagentClientError` → `_client_error` 中文话术；单条 ingest 失败不影响其他条。

### 3. 种子数据（`mcp_server/faq_seed.json`）

- 20 条经核实 SQL 的 FAQ 条目（从 ReportAgent `schema_faq.json` 迁来，作为本仓库权威种子）：字段 `{id, question, keywords, tables, sql, note}`。
- 灌库方式：调 `ingest_faq` 工具（`faqs` 传种子内容），或按需由 Claude Code / 脚本触发。`search_faq` 只读不生成。

## Files to change

- `mcp_server/render.py`：`faq_filename` / `render_faq_doc`。
- `mcp_server/server.py`：`_faq_kb_name` / `cmd_ingest_faq` / `cmd_search_faq` / `cmd_list_faq_docs` + `_DISPATCH` + `handle_list_tools` 注册。
- `mcp_server/faq_seed.json`（新建）：20 条 FAQ 种子。
- 测试：`tests/unit/test_faq_render.py`（新建）、`tests/unit/test_faq_server.py`（新建）。
- `docs/plans/2026-08-10-schema-faq-mcp.md`（本文件）+ `docs/plans/README.md` 索引。

## Reused existing utilities

- `mcp_server/client.py::RagentClient`（`ensure_kb`/`upload_document`/`wait_indexed`/`retrieve`/`list_documents`）——FAQ 灌库/检索全白拿，不新增 HTTP 调用面。
- `mcp_server/render.py` 的确定性文件名幂等范式（`api_filename`）。
- `mcp_server/server.py` 的 `_client_context` / `_client_error` / `cmd_*` / `_DISPATCH` / `handle_list_tools` 结构——直接扩展，不重构。
- ragent-py 既有 `POST /api/v1/retrieve`（混合检索 + RRF + embedding 熔断降级）。

## Verification

```bash
# ragent-py（rag 环境）
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_faq_render.py tests/unit/test_faq_server.py -q
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q          # 全量回归
```

新增离线单测：

1. `render_faq_doc`：标题=问题、含关键词/涉及表/示例 SQL 代码块/要点；`faq_filename` 幂等命名。
2. `cmd_ingest_faq`（mock client）：缺 `faqs` → 缺参文案；单条缺 `question` → error 记入结果不中断；成功条 → 上传 + 轮询 indexed；`upload_document` 未返回 document_id → upload_failed。
3. `cmd_search_faq`（mock client）：缺 `query` → 缺参；`top_k` 非整数 → 提示；空命中 → `FAQ 无匹配`；命中 → `{matches, degraded}`。
4. `handle_list_tools`：`ingest_faq` / `search_faq` / `list_faq_docs` 均在列。

手工冒烟（需 ragent-py + docker PG 起）：

1. `ingest_faq` 灌 seed → 全部 `indexed`，`chunk_count > 0`；重跑 → 全部 unchanged。
2. `search_faq("退货率")` → 命中退货率 FAQ（text 含问题+SQL+要点）。
3. `search_faq("毛利率")` → 命中毛利率 FAQ。
4. 错误路径：错误密码 → 401 话术；停 ragent-py → 不可达话术。

## Explicitly NOT doing

- **不做** 语义向量之外的排序/重排定制——复用 `/retrieve` 混合检索，SQL Agent 消费 `matches[].text`。
- **不做** 把 FAQ 并入「数据字典」KB——独立 KB 隔离，`search_dictionary` 与 `search_faq` 互不污染。
- **不做** FAQ 的自动同步/变更侦测——`ingest_faq` 手工/工具触发（与字典桥一致）。
- **不做** ReportAgent 侧此刻的改动——本 plan 只做 ragent-py（Phase A）；ReportAgent MCP client（Phase B）另开 plan。
- **不做** 50+ 条灌水——20 条经核实 SQL 优先，宁缺毋滥；扩充是长期饲养。