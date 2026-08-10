"""MCP 工具分发：stub 掉 client/introspect，只测 server 编排与错误契约。"""
import asyncio
import json

import pytest

mcp = pytest.importorskip("mcp")  # mcp SDK 未安装时跳过本文件

from mcp_server import server as srv
from mcp_server.client import RagentClientError


class FakeClient:
    def __init__(self, kb_id="kb-9", upload=None, wait=None, retrieve=None, docs=None):
        self.kb_id = kb_id
        self._upload = upload or {"document_id": "d1", "filename": "f.md", "status": "processing", "chunk_count": 0}
        self._wait = wait or {"document_id": "d1", "status": "indexed", "chunk_count": 2}
        self._retrieve = retrieve or {"items": [], "degraded": False}
        self._docs = docs if docs is not None else []

    async def ensure_kb(self, name, visibility="internal"):
        return self.kb_id

    async def upload_document(self, kb_id, filename, content):
        self.last_upload = (filename, content)
        return dict(self._upload)

    async def wait_indexed(self, document_id, timeout_s=180.0, interval_s=2.0):
        return dict(self._wait)

    async def retrieve(self, query, kb_ids, top_k=5):
        return dict(self._retrieve)

    async def list_documents(self, kb_id, limit=200):
        return list(self._docs)

    async def aclose(self):
        pass


def test_ingest_table_schemas_happy(monkeypatch):
    monkeypatch.setenv("DICT_PG_DSN", "postgresql://fake")
    monkeypatch.setattr(srv, "introspect_schema", lambda dsn, schema, tables=None: [{
        "schema": "public", "table": "fact_sales", "table_comment": "销售",
        "columns": [{"name": "sale_id", "type": "integer", "comment": "主键", "enums": None, "fk": None}],
    }])
    fake = FakeClient()
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_ingest_table_schemas({"schema": "public"})))
    assert out[0]["status"] == "indexed"
    assert fake.last_upload[0] == "dict-table_public_fact_sales.md"


def test_ingest_table_schemas_missing_dsn(monkeypatch):
    monkeypatch.delenv("DICT_PG_DSN", raising=False)
    out = asyncio.run(srv.cmd_ingest_table_schemas({}))
    assert "DICT_PG_DSN" in out


def test_ingest_ragent_error_is_text(monkeypatch):
    monkeypatch.setenv("DICT_PG_DSN", "postgresql://fake")
    monkeypatch.setattr(srv, "introspect_schema", lambda dsn, schema, tables=None: [
        {"schema": "public", "table": "t", "table_comment": "", "columns": []}])

    class Boom(FakeClient):
        async def ensure_kb(self, name, visibility="internal"):
            raise RagentClientError("登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD")

    monkeypatch.setattr(srv, "RagentClient", lambda: Boom())
    out = asyncio.run(srv.cmd_ingest_table_schemas({}))
    assert "登录失败" in out


def test_upsert_api_dictionary_renders_and_uploads(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_upsert_api_dictionary({
        "name": "orders", "description": "订单接口",
        "fields": [{"name": "amt", "type": "number", "required": True, "desc": "金额"}],
    })))
    assert out["status"] == "indexed"
    fname, content = fake.last_upload
    assert fname == "dict-api_orders.md"
    assert "| amt | number | 是 | 金额 |" in content


def test_search_dictionary_empty_semantics(monkeypatch):
    fake = FakeClient(kb_id="kb-9", retrieve={"items": [], "degraded": False})
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = asyncio.run(srv.cmd_search_dictionary({"query": "不存在", "top_k": 3}))
    assert "字典库无匹配" in out


def test_list_dictionary_docs(monkeypatch):
    fake = FakeClient(docs=[{"document_id": "d1", "filename": "dict-table_public_fact_sales.md",
                             "status": "indexed", "kb_id": "kb-9", "chunk_count": 2}])
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_list_dictionary_docs({})))
    assert out[0]["filename"].startswith("dict-")


def test_mcp_tool_surface():
    tools = asyncio.run(srv.handle_list_tools())
    names = {t.name for t in tools}
    # 字典桥 4 工具 + Schema FAQ 3 工具（ingest_faq/search_faq/list_faq_docs）
    assert names == {
        "ingest_table_schemas", "upsert_api_dictionary", "search_dictionary", "list_dictionary_docs",
        "ingest_faq", "search_faq", "list_faq_docs",
    }


def test_ingest_table_schemas_partial_failure(monkeypatch):
    """(a) 第二张表抛 RagentClientError → results 数组长度 2，第二项有 error，第一项正常。"""
    monkeypatch.setenv("DICT_PG_DSN", "postgresql://fake")
    monkeypatch.setattr(srv, "introspect_schema", lambda dsn, schema, tables=None: [
        {"schema": "public", "table": "t_ok", "table_comment": "", "columns": []},
        {"schema": "public", "table": "t_bad", "table_comment": "", "columns": []},
    ])

    class PartialBoom(FakeClient):
        async def upload_document(self, kb_id, filename, content):
            if filename.endswith("dict-table_public_t_bad.md"):
                raise RagentClientError("第二张表上传失败：仅供测试")
            return dict(self._upload)

    monkeypatch.setattr(srv, "RagentClient", lambda: PartialBoom())
    out = json.loads(asyncio.run(srv.cmd_ingest_table_schemas({"schema": "public"})))
    assert len(out) == 2, f"期望 2 项结果，实际 {len(out)}"
    assert out[0]["status"] == "indexed"
    assert out[0]["table"].endswith("t_ok")
    assert out[1]["status"] == "error"
    assert out[1]["table"].endswith("t_bad")
    assert "第二张表上传失败" in out[1]["error"]


def test_handle_call_tool_unknown_tool_returns_text():
    """(b) 未知工具 dispatch → TextContent 文本，无 raise。"""
    result = asyncio.run(srv.handle_call_tool("does_not_exist", {}))
    assert len(result.content) == 1
    assert isinstance(result.content[0], mcp.types.TextContent)
    assert "未知工具" in result.content[0].text
    assert "does_not_exist" in result.content[0].text


def test_cmd_returns_text_when_ragent_url_missing(monkeypatch):
    """(c) RAGENT_URL 未配（RagentClient 构造即抛）→ cmd_* 返文本，无 raise。"""
    monkeypatch.delenv("RAGENT_URL", raising=False)
    monkeypatch.setenv("DICT_PG_DSN", "postgresql://fake")  # 让 ingest 越过 DSN 检查

    class NoUrlClient(FakeClient):
        def __init__(self, *args, **kwargs):
            raise RagentClientError("RAGENT_URL 未配置")

    monkeypatch.setattr(srv, "introspect_schema", lambda dsn, schema, tables=None: [
        {"schema": "public", "table": "t", "table_comment": "", "columns": []}
    ])
    monkeypatch.setattr(srv, "RagentClient", lambda: NoUrlClient())
    for cmd, args in [
        (srv.cmd_ingest_table_schemas, {"schema": "public"}),
        (srv.cmd_upsert_api_dictionary, {"name": "x", "fields": [{"name": "a", "type": "string"}]}),
        (srv.cmd_search_dictionary, {"query": "q"}),
        (srv.cmd_list_dictionary_docs, {}),
    ]:
        out = asyncio.run(cmd(args))
        assert "RAGENT_URL" in out, f"{cmd.__name__} 未透传 RAGENT_URL 错误: {out!r}"


def test_ingest_table_filename_error_keeps_batch(monkeypatch):
    """table_filename 抛非 RagentClientError 时，fname 已初始化，except 块可安全引用。

    终审 I-2：修复前 except 块用 `fname if "fname" in locals() else ""` 兜底，
    只覆盖 RagentClientError；此处断言 fname 预初始化后不再有 UnboundLocalError 风险。
    """
    monkeypatch.setenv("RAGENT_URL", "http://fake:8000")
    monkeypatch.setenv("DICT_PG_DSN", "postgresql://fake")
    monkeypatch.setattr(srv, "introspect_schema", lambda dsn, schema, tables=None: [
        {"schema": "public", "table": "t_ok", "table_comment": "", "columns": []},
        {"schema": "public", "table": "t_bad", "table_comment": "", "columns": []},
    ])

    real_table_filename = srv.table_filename

    def flaky_filename(schema, table):
        if table == "t_bad":
            raise RagentClientError("文件名渲染失败：仅供测试")
        return real_table_filename(schema, table)

    monkeypatch.setattr(srv, "table_filename", flaky_filename)
    monkeypatch.setattr(srv, "RagentClient", lambda: FakeClient())
    out = json.loads(asyncio.run(srv.cmd_ingest_table_schemas({"schema": "public"})))
    assert len(out) == 2, f"期望 2 项结果，实际 {len(out)}"
    assert out[0]["status"] == "indexed"
    assert out[1]["status"] == "error"
    assert out[1]["filename"] == "", f"fname 未初始化为空串: {out[1]['filename']!r}"
    assert "文件名渲染失败" in out[1]["error"]
