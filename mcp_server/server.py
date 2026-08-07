"""ragent-py 数据字典 MCP 服务（stdio）。

mcp>=2.0.0 stdio 服务（mcp 2.0 protocol，`Server(...)` 用 `on_list_tools`/`on_call_tool`
构造期参数绑定；模块级 `handle_*` async 函数便于单测，适配层 `_on_*` 在 `server.run`
前一次性桥接）。

启动: D:/miniConda/envs/rag/python.exe -m mcp_server.server
配置（env，绝不进工具入参）:
  RAGENT_URL        ragent-py 地址
  RAGENT_USER / RAGENT_PASSWORD   服务账号（需 kb.create / doc.upload / doc.read_all）
  DICT_PG_DSN       自省用只读 PG 连接串
  DICT_KB_NAME      字典知识库名，默认「数据字典」
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import Server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from mcp_server.client import RagentClient, RagentClientError
from mcp_server.introspect import introspect_schema
from mcp_server.render import api_filename, render_api_doc, render_table_doc, table_filename


def _kb_name() -> str:
    return os.getenv("DICT_KB_NAME", "数据字典")


@asynccontextmanager
async def _client_context():
    """收敛 4 个 cmd_* 的 RagentClient() 构造 + aclose 清理。

    构造错（如 RAGENT_URL 未配）会作为构造期异常透传——调用方统一 `except RagentClientError` 转中文文本。
    """
    client = RagentClient()
    try:
        yield client
    finally:
        await client.aclose()


def _client_error(exc: BaseException) -> str:
    """统一把 RagentClientError 转中文文本。"""
    if isinstance(exc, RagentClientError):
        return str(exc)
    return f"客户端异常: {exc}"


# ── 工具实现（模块级函数，便于单测 stub） ─────────────────────


async def cmd_ingest_table_schemas(arguments: dict) -> str:
    dsn = os.getenv("DICT_PG_DSN", "")
    if not dsn:
        return "DICT_PG_DSN 未配置，无法自省数据库"
    schema = arguments.get("schema") or "public"
    tables = arguments.get("tables") or None
    try:
        infos = await asyncio.to_thread(introspect_schema, dsn, schema, tables)
    except Exception as exc:
        return f"数据库自省失败: {exc}"
    try:
        async with _client_context() as client:
            kb_id = await client.ensure_kb(_kb_name())
            results = []
            for info in infos:
                table_ref = f"{info['schema']}.{info['table']}"
                # 先初始化：table_filename 抛错时 except 块仍能引用 fname
                fname = ""
                try:
                    fname = table_filename(info["schema"], info["table"])
                    md = render_table_doc(schema=info["schema"], table=info["table"],
                                          table_comment=info["table_comment"], columns=info["columns"])
                    up = await client.upload_document(kb_id, fname, md)
                    doc_id = up.get("document_id") if isinstance(up, dict) else None
                    if not doc_id:
                        results.append({
                            "table": table_ref,
                            "filename": fname,
                            "document_id": None,
                            "status": "upload_failed",
                            "chunk_count": 0,
                            "error": "upload_document 未返回 document_id",
                        })
                        continue
                    doc = await client.wait_indexed(doc_id)
                    results.append({
                        "table": table_ref,
                        "filename": fname,
                        "document_id": doc.get("document_id", doc_id),
                        "status": doc.get("status", "unknown"),
                        "chunk_count": doc.get("chunk_count", 0),
                        "error": doc.get("error_message", ""),
                    })
                except RagentClientError as exc:
                    # 单表失败不影响其他表继续灌入
                    results.append({
                        "table": table_ref,
                        "filename": fname,
                        "document_id": None,
                        "status": "error",
                        "chunk_count": 0,
                        "error": str(exc),
                    })
                    continue
        return json.dumps(results, ensure_ascii=False, indent=2)
    except RagentClientError as exc:
        return _client_error(exc)
    except Exception as exc:
        return _client_error(exc)


async def cmd_upsert_api_dictionary(arguments: dict) -> str:
    name = (arguments.get("name") or "").strip()
    if not name:
        return "缺少必填参数 name"
    fields = arguments.get("fields") or []
    if not fields:
        return "缺少必填参数 fields（至少一个字段）"
    md = render_api_doc(
        name=name,
        description=arguments.get("description", ""),
        protocol=arguments.get("protocol", "http"),
        endpoint=arguments.get("endpoint", ""),
        auth=arguments.get("auth", ""),
        fields=fields,
    )
    try:
        async with _client_context() as client:
            kb_id = await client.ensure_kb(_kb_name())
            fname = api_filename(name)
            up = await client.upload_document(kb_id, fname, md)
            doc_id = up.get("document_id") if isinstance(up, dict) else None
            if not doc_id:
                return f"upload_document 未返回 document_id：{up}"
            doc = await client.wait_indexed(doc_id)
            return json.dumps({
                "name": name,
                "filename": fname,
                "document_id": doc.get("document_id", doc_id),
                "status": doc.get("status", "unknown"),
                "chunk_count": doc.get("chunk_count", 0),
                "error": doc.get("error_message", ""),
            }, ensure_ascii=False, indent=2)
    except RagentClientError as exc:
        return _client_error(exc)
    except Exception as exc:
        return _client_error(exc)


async def cmd_search_dictionary(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "缺少必填参数 query"
    try:
        top_k = int(arguments.get("top_k") or 5)
    except ValueError as e:
        return f"参数 top_k 应为整数：{e}"
    try:
        async with _client_context() as client:
            kb_id = await client.ensure_kb(_kb_name())
            data = await client.retrieve(query, [kb_id], top_k=top_k)
            items = data.get("items", [])
            if not items:
                return f"字典库无匹配：{query}"
            return json.dumps({"matches": items, "degraded": data.get("degraded", False)},
                              ensure_ascii=False, indent=2)
    except RagentClientError as exc:
        return _client_error(exc)
    except Exception as exc:
        return _client_error(exc)


async def cmd_list_dictionary_docs(arguments: dict) -> str:
    try:
        async with _client_context() as client:
            kb_id = await client.ensure_kb(_kb_name())
            docs = await client.list_documents(kb_id)
            return json.dumps(docs, ensure_ascii=False, indent=2)
    except RagentClientError as exc:
        return _client_error(exc)
    except Exception as exc:
        return _client_error(exc)


# ── MCP 接线 ──


async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="ingest_table_schemas",
            description=(
                "自省 PostgreSQL 表结构（COMMENT 语义 + FK + 低基数枚举），渲染为数据字典文档并灌入 RAG 字典知识库。\n"
                "输入：schema（默认 public）、tables（可选，表名过滤数组）。\n"
                "输出：每表 JSON 结果 {table, filename, document_id, status, chunk_count, error}。\n"
                "用于：首次建库、表结构或注释变更后刷新字典。重复执行幂等（同名文档增量更新）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "schema": {"type": "string", "description": "PG schema，默认 public"},
                    "tables": {"type": "array", "items": {"type": "string"}, "description": "可选表名过滤"},
                },
            },
        ),
        Tool(
            name="upsert_api_dictionary",
            description=(
                "登记/更新一个接口的字段字典（含长连接协议标记），灌入 RAG 字典知识库。\n"
                "输入：name（必填）、description、protocol（http/websocket/sse/long_poll）、endpoint、auth、\n"
                "fields: [{name, type, required, desc, example, message?}]（流式接口用 message 标注消息类型）。\n"
                "输出：{filename, document_id, status, chunk_count, error}。同名全量覆盖（幂等）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "protocol": {"type": "string", "enum": ["http", "websocket", "sse", "long_poll"]},
                    "endpoint": {"type": "string"},
                    "auth": {"type": "string"},
                    "fields": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["name", "fields"],
            },
        ),
        Tool(
            name="search_dictionary",
            description=(
                "在数据字典知识库中检索字段/表/接口含义（混合检索，只检索不生成）。\n"
                "输入：query（必填，中文自然语言）、top_k（默认 5）。\n"
                "输出：匹配 chunk 列表（内容片段 + 来源文档 + 分数）；无匹配时返回「字典库无匹配：<query>」。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_dictionary_docs",
            description="列出字典知识库中已登记的字典文档及摄入状态。无输入。",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


_DISPATCH = {
    "ingest_table_schemas": cmd_ingest_table_schemas,
    "upsert_api_dictionary": cmd_upsert_api_dictionary,
    "search_dictionary": cmd_search_dictionary,
    "list_dictionary_docs": cmd_list_dictionary_docs,
}


async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
    handler = _DISPATCH.get(name)
    if handler is None:
        return CallToolResult(content=[TextContent(type="text", text=f"未知工具：{name}")])
    text = await handler(arguments or {})
    return CallToolResult(content=[TextContent(type="text", text=text)])


# mcp 2.0 的 Server 在构造期接收 on_list_tools / on_call_tool（替代 1.x 的装饰器）。
# 这里再包一层把 SDK 协议对象转成 handle_* 的纯输入，便于协议与编排解耦。
async def _on_list_tools(_ctx, _params) -> ListToolsResult:
    return ListToolsResult(tools=await handle_list_tools())


async def _on_call_tool(_ctx, params) -> CallToolResult:
    return await handle_call_tool(params.name, params.arguments or {})


server = Server(
    "ragent-dictionary",
    on_list_tools=_on_list_tools,
    on_call_tool=_on_call_tool,
)


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
