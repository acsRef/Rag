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
    assert names == {"ingest_table_schemas", "upsert_api_dictionary", "search_dictionary", "list_dictionary_docs"}