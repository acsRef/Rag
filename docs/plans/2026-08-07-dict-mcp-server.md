# 数据字典 MCP stdio 服务（dict-mcp-server）实施计划

> 状态: 已完成（任务 A7）—— 7 例单测全绿 + 192 passed 无回归。

## Context

延续 A4 / A5 / A6 已交付的数据字典桥（render / introspect / client），把 4 个工具（ingest_table_schemas / upsert_api_dictionary / search_dictionary / list_dictionary_docs）拼成 stdio MCP 服务，让 Claude Code 等 MCP 客户端能 stdio 协议调用。

CLAUDE.md 的 plan 驱动原则要求：多文件 + 设计决策 → 先写 plan。本任务为单一职责（仅实现 server 装配层），沿用 A4-A6 既有抽象；决策点是 mcp SDK 版本兼容（`pip install mcp` 默认拿到 2.0，与计划假设的 1.x 装饰器 API 不同）。

## Design

### 文件改动

- 新建 `mcp_server/server.py`（A7 主交付物）
- 新建 `mcp_server/requirements.txt`（`mcp>=1.0.0` + `httpx` + `psycopg2-binary`）
- 新建 `tests/unit/test_dict_server.py`（7 例）

### 模块划分

1. **工具编排函数**（`cmd_ingest_table_schemas` / `cmd_upsert_api_dictionary` / `cmd_search_dictionary` / `cmd_list_dictionary_docs`）—— 模块级 `async def`，便于单测直接 `await` 并用 `monkeypatch` stub 掉 `introspect_schema` / `RagentClient`。
2. **MCP 接线** —— `handle_list_tools()` 返回 `list[Tool]`，`handle_call_tool(name, arguments)` 返回 `CallToolResult`；两者也是模块级 async 函数，单测独立 import 验证。
3. **协议适配层** —— `_on_list_tools` / `_on_call_tool` 把 mcp 2.0 协议对象（`ServerRequestContext` + `CallToolRequestParams`）转换成 `handle_*` 的纯输入；协议与编排解耦，将来 mcp SDK 升级只需改这一层。
4. **Server 单例** —— `Server("ragent-dictionary", on_list_tools=..., on_call_tool=...)`，`main()` 用 `stdio_server()` 驱动。

### 错误契约（4 工具统一）

- 配置缺失 / 必填参数缺失 / 自省失败 / `RagentClientError` → 一律 `return "<可读中文消息>"`（写进 `TextContent.text`），**永不**抛栈出去给 MCP 客户端。
- `RagentClient.aclose()` 包在每个工具的 `try/finally`，避免连接泄漏。
- KB 名走 env `DICT_KB_NAME`（默认「数据字典」），不暴露为工具入参——凭据策略与 A6 对齐。

### mcp 2.0 vs 1.x 兼容

计划原文用 `@server.list_tools()` / `@server.call_tool()` 装饰器（mcp 1.x）。`pip install mcp>=1.0.0` 实际装到 2.0，装饰器已被移除，`Server.__init__` 改为接收 `on_list_tools` / `on_call_tool` 参数。

采用方案：保留 `handle_list_tools` / `handle_call_tool` 模块级 async 函数（与单测契约对齐，`asyncio.run(srv.handle_list_tools())`），加薄薄的 `_on_*` 适配层把它们绑给 `Server` 构造期参数。这样：
- 单测不依赖 mcp SDK 版本（除 `import mcp` 的 importorskip 守护）。
- 未来升级 SDK 改 `_on_*` 即可，编排逻辑不动。

### 重用既有抽象

- `mcp_server.client.RagentClient` / `RagentClientError`（A6）：唯一 HTTP 客户端。
- `mcp_server.introspect.introspect_schema`（A5）：同步阻塞，`asyncio.to_thread` 包裹避免阻事件循环。
- `mcp_server.render.render_table_doc` / `render_api_doc` / `table_filename` / `api_filename`（A4）：Markdown 渲染 + 幂等文件名。

## Verification

- 单测：`D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_server.py -v` → 7 passed。
- 全量回归：`D:/miniConda/envs/rag/python.exe -m pytest tests/unit/` → 192 passed（= 185 旧 + 7 新），无新增失败。
- 启动验证（手工）：`D:/miniConda/envs/rag/python.exe -m mcp_server.server` 应能起 stdio 服务不崩（不强制接 PG / ragent-py，因需 env 配合；CI 用单测覆盖）。

## Explicitly NOT doing

- 不实现 KB 创建/删除/列表工具——只暴露字典桥消费面（读 + 自省写入）。
- 不把 RAGENT_URL / RAGENT_USER / RAGENT_PASSWORD 暴露为工具入参——凭据只走 env。
- 不在工具返回中区分 status / isError——MCP 客户端只看 `TextContent.text`；失败语义用中文消息字符串表达。
- 不引入认证 / 限流——MCP stdio 协议假设本地受信调用方，鉴权由 MCP 宿主负责。
- 不修改 `mcp_server/client.py` / `introspect.py` / `render.py`——A7 是纯装配层。

## 自审（完成后追加）

- 4 工具契约对齐描述与 input_schema：4 个 Tool 定义齐，inputSchema 必填字段明示（name/fields, query）。
- 错误路径都返可读文本而非栈：DSN 缺失、introspect 异常、`RagentClientError`、缺 name/fields 全部返回中文可读字符串。
- finally 保证 aclose：4 个 cmd 函数均 `try/finally` + `await client.aclose()`。
- KB 名走 env：`_kb_name()` 读 `DICT_KB_NAME`，默认「数据字典」。
