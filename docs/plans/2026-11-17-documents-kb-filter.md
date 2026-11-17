> 状态: 已完成（2026-11-17）

# /api/v1/documents 支持按知识库过滤（kb_id）

## Context

`GET /api/v1/documents` 之前**没有 `kb_id` 参数**——FastAPI 会静默忽略未知 query 参数，
于是 admin 调 `?kb_id=<临时库>` 拿到的是**全库**。真实事故（2026-11-17）：
ReportAgent 的评测平台按「列本库文档 → 逐个删」清理临时知识库，结果删掉了全库
14 份文档 / 1391 个 chunk（三一重工 3 份年报 + 数据字典 + FAQ）。

## Design

`list_documents(..., kb_id: str | None = None)`：

- 给了 `kb_id` → `query.filter(Document.kb_id == kb_id)`；
- 没给 → **不加**该条件（保持向后兼容，旧调用方行为不变）；
- 非 admin 的 `owner_id` 过滤**继续叠加**——`kb_id` 不能成为绕过权限的旁路。

## Files to change

| 文件 | 变更 |
|---|---|
| `app/api/documents.py` | `list_documents` 增 `kb_id` 参数 + filter（含 2026-11-17 事故注释） |
| `tests/unit/test_documents_list_filter.py` | **新增** 3 例：给了 kb_id 会加条件 / 不给不加 / 非 admin 仍叠加 owner |

## Verification

```
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_documents_list_filter.py -q
→ 3 passed
```

## Explicitly NOT doing

- 不改前端与 MCP 客户端（它们本来就按 `kb_id` 传参，只是之前被忽略——修完即生效）；
- 不加分页游标（现状 limit/offset 够用）；
- 不做"按库批量删除"端点（删除仍逐文档 + 权限校验，避免造出新的破坏性入口）。
