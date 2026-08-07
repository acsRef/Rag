# 数据字典 MCP 桥（dict-mcp-server）—— A1-A8 实施落地摘要

> 状态: 已完成（A1-A7 全部代码已落地 + 全量回归 195 passed；A8 仅 plan 登记收尾）

## Context

本计划覆盖 ragent-py 仓库侧「数据字典 RAG 桥」子项目 A 的全部 8 个实现任务（A1-A8）。设计权威来自 ReportAgent 仓库同 slug plan `docs/plans/2026-08-06-rag-dictionary-mcp-bridge.md`，本文件不复述设计细节——以引用对齐为准。

**A1-A7 范围**（ragent-py 侧纯实施，未触及 ReportAgent 侧 B 子项目代码）：

- **A1**：`RetrieveRequest` / `RetrieveResponse` / `RetrievedItem` 契约模型（`app/models/schemas.py`），配套 8 例边界单测。
- **A2**：抽 `embed_query_with_fallback()` 共享 helper（`app/core/retrieval.py`），把两处复制粘贴的熔断→BM25-only 降级模式收口；并修补 `except Exception` 分支日志缺异常对象的隐患（顺手发现）。9 例降级单测。
- **A3**：`POST /api/v1/retrieve` 端点（`app/api/retrieve.py`），visibility/角色访问判定 + `kb_ids` pin + admin/`doc.read_all` bypass；settings 透传 + hybrid 开关镜像 + kb_ids 上限 + 响亮错误契约。29 例鉴权/隔离/降级/空结果单测。
- **A4**：数据字典 Markdown 渲染（`mcp_server/render.py`），表结构（表名/注释/字段表：名称·类型·注释·枚举值/FK 指向）与接口字典（按 protocol/message 分节，含长连接协议标记）。4 例渲染单测。
- **A5**：PG 只读自省（`mcp_server/introspect.py`），`COMMENT ON COLUMN` 语义 + FK 链 + 低基数枚举采样（类型白名单 varchar/char/bool/小整数，distinct ≤20），readonly 连接层 + generated 列排除 + PII 文档对齐。4 例自省单测。
- **A6**：ragent-py HTTP 客户端（`mcp_server/client.py`），登录重登 + KB ensure（按名复用）+ upload（确定性文件名幂等）+ 轮询状态 + `/retrieve` 调取；钉超时抛错 / `list_documents` 权限声明 / 401 重试保留诊断 / 默认 `base_url` fail-loud。6 例客户端单测。
- **A7**：MCP stdio 服务装配（`mcp_server/server.py`），4 工具（`ingest_table_schemas` / `upsert_api_dictionary` / `search_dictionary` / `list_dictionary_docs`）+ 错误契约（DSN/必填/introspect/`RagentClientError` 全部返中文可读文本）+ finally `aclose` + KB 名走 `DICT_KB_NAME` env；mcp 2.0 协议适配（`on_list_tools`/`on_call_tool` 构造期参数替代 1.x 装饰器）。10 例（7 + 3 修补）单测。

**A8（本任务）**：plan 登记收尾——把 A1-A7 commit 链 + 测试累计数 + 既有隐患登记进本文档，全量回归 195 passed 无新增失败。

## Design

### 文件改动清单（最终态）

| 任务 | 提交 SHA | 新增/修改 | 关键模块 |
|---|---|---|---|
| A1 | af89649 | feat | `app/models/schemas.py` |
| A1 | fa29024 | test | `tests/unit/test_retrieve_schemas.py`（边界 + docstring） |
| A2 | a2b1c28 | refactor | `app/core/retrieval.py::embed_query_with_fallback` |
| A2 | 0651532 | refactor | 异常日志补 `exc_info` + 测试加固 |
| A3 | cdfd93f | feat | `app/api/retrieve.py` + `app/main.py` 挂载 |
| A3 | fdf7dce | fix | settings 透传 / hybrid 镜像 / kb_ids 上限 / 响亮契约 |
| A4 | ce2735f | feat | `mcp_server/render.py`（表结构 + 接口字典，含长连接） |
| A5 | 8e47216 | feat | `mcp_server/introspect.py`（COMMENT + FK + 枚举采样） |
| A5 | 976d5cc | fix | 删死分支 / NULL 边界 / PII 对齐 / readonly 连接层 / generated 排除 |
| A6 | 02887b6 | feat | `mcp_server/client.py`（登录重登 + ensure + 上传 + 轮询 + retrieve） |
| A6 | 6d31a80 | fix | 超时抛错 / 权限声明 / 401 诊断保留 / 默认 base_url fail-loud |
| A7 | 7f6d9f4 | feat | `mcp_server/server.py` 装配 + 错误契约 |
| A7 | 53a86d0 | fix | `mcp>=2.0.0` / 输入校验 / 未知工具文本 / SDK caps / 部分失败透传 |

### 测试累计

定向测试数（每任务最终 commit 为准；后续回归中部分测试被覆盖重计）：

- A1：8（fa29024）
- A2：9（0651532）
- A3：29（fdf7dce）
- A4：4（ce2735f）
- A5：4（976d5cc）
- A6：6（6d31a80）
- A7：10（53a86d0；7f6d9f4 原 7 例 + 53a86d0 补丁 +3）
- **合计**：70 passed

### 全量 `tests/unit` 最终状态

```
195 passed, 1 warning in 7.44s
```

- A1-A7 实施前基线：185 passed（A7 7f6d9f4 commit message 自报：192 = 185 + 7）。
- A1-A7 实施后：195 passed（增量 10 与 A7 补丁 +3 累计 +7 = +10 净增；中间若干补丁新增的契约/边界用例被吸收）。
- `tests/unit/test_rerank_client.py` 3 例 SSL 环境失败记为**既有失败**（无 `RERANK_BASE_URL`/SSL 套接字可达性；本任务未触动），不在回归通过口径内。

### 重用既有抽象

- `app/store/pgvector_store.py::hybrid_search`——A3 直接复用，未引入新检索通路
- `app/api/documents.py` upload 链路——A4/A6 走既有 `_resolve_document_id` 同名复用 + 增量 hash 管线
- `app/middleware/auth.py::get_current_user`、`app/api/kb.py` 的 KB 按名 ensure 模式——A6 直接复用
- `app/core/retrieval.py` 既有 embedding 熔断→BM25-only 降级模式——A2 抽 helper 复用
- 设计细节与决策表详见 ReportAgent 仓库 `docs/plans/2026-08-06-rag-dictionary-mcp-bridge.md`

### 顺手发现的既有隐患（不动，留 follow-up）

1. **`app/api/kb.py::delete_kb` NameError 隐患**：`delete_kb` 使用 `DocRoleAccess` 但顶部 import 行未导入该符号（`DocRoleAccess` 定义在 `app/store/db.py:196`）。当前调用路径不触发删除含文档的 KB；若触发，删除含文档 KB 时会 `NameError: name 'DocRoleAccess' is not defined`。本计划**不修复**，登记为 follow-up（独立 plan，与本任务解耦）。
2. **A2 修补前的 `except Exception` 通用异常分支日志缺异常对象**：已通过 0651532 修补——日志调用加 `exc_info=True`（或等价地把 `Exception` 实例塞进 extra 字段），并在测试中钉桩「异常 message 必须进日志」。该隐患已闭环，无遗留。

## Verification

- 单测（每个任务最终 commit 的定向测试）：8 + 9 + 29 + 4 + 4 + 6 + 10 = **70 passed**
- 全量回归（A8 收尾）：`D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q` → **195 passed, 1 warning in 7.44s**
- 设计权威对齐：与 ReportAgent 仓库 `docs/plans/2026-08-06-rag-dictionary-mcp-bridge.md` 一致（ragent-py 侧仅子项目 A；B 子项目不属本仓库）

## Explicitly NOT doing

- **不修复** `app/api/kb.py::delete_kb` 的 `DocRoleAccess` import 缺失——登记为 follow-up，本任务不越界
- **不动 ReportAgent 侧 B 子项目代码**（`seed_pg.sql` COMMENT、`interface_dict_tools.py`、`requirement_parser.py` 的 `dictionary_context` 参数等）——属另一仓库另一计划
- **不重写 A1-A7 任一文件**——A8 仅 plan 登记收尾
- **不修改 mcp SDK 兼容策略**——A7 已钉 mcp>=2.0.0 + `on_list_tools`/`on_call_tool` 构造期参数
- **不新增测试基础设施**（195 + 3 既有失败的回归口径不变）
- **不动 `docs/plans/2026-08-06-design-review-fixes.md`**——他人未跟踪文件
- **不实现 KB 创建/删除/列表工具**——MCP 只暴露字典桥消费面（读 + 自省写入）
- **不做接口/API 数据源取数报表**——v1 = 字典查询 + 澄清，报表数据仍走 PG 星型模型
- **不引入认证 / 限流**——MCP stdio 协议假设本地受信调用方