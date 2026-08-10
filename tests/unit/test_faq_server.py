"""FAQ MCP 工具分发：stub 掉 client，只测 server 编排与错误契约。"""
import asyncio
import json

import pytest

mcp = pytest.importorskip("mcp")

from mcp_server import server as srv
from mcp_server.client import RagentClientError


class FakeClient:
    def __init__(self, kb_id="kb-faq", upload=None, wait=None, retrieve=None, docs=None):
        self.kb_id = kb_id
        self._upload = upload or {"document_id": "d1", "filename": "f.md", "status": "processing", "chunk_count": 0}
        self._wait = wait or {"document_id": "d1", "status": "indexed", "chunk_count": 1}
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


_FAQ = {
    "id": "faq-001",
    "question": "各区域销售额排名",
    "keywords": ["区域", "销售额", "排名"],
    "tables": ["fact_sales", "dim_region"],
    "sql": "SELECT r.region_name, SUM(f.total_amount) AS 销售额 FROM fact_sales f JOIN dim_region r ON f.region_id=r.region_id GROUP BY r.region_name ORDER BY 销售额 DESC",
    "note": "ORDER BY 聚合别名降序为排名",
}


def test_ingest_faq_happy(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_ingest_faq({"faqs": [dict(_FAQ)]})))
    assert out[0]["status"] == "indexed"
    assert out[0]["id"] == "faq-001"
    assert fake.last_upload[0] == "faq-faq-001.md"
    assert "# 各区域销售额排名" in fake.last_upload[1]


def test_ingest_faq_missing_faqs(monkeypatch):
    out = asyncio.run(srv.cmd_ingest_faq({}))
    assert "缺少必填参数 faqs" in out


def test_ingest_faq_missing_question(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_ingest_faq({"faqs": [{"sql": "SELECT 1"}]})))
    assert out[0]["status"] == "error"
    assert "缺少 question" in out[0]["error"]


def test_ingest_faq_partial_failure_keeps_batch(monkeypatch):
    class PartialBoom(FakeClient):
        async def upload_document(self, kb_id, filename, content):
            if "faq-bad.md" in filename:
                raise RagentClientError("坏条目上传失败：仅供测试")
            return dict(self._upload)

    monkeypatch.setattr(srv, "RagentClient", lambda: PartialBoom())
    out = json.loads(asyncio.run(srv.cmd_ingest_faq({
        "faqs": [dict(_FAQ), {"id": "bad", "question": "坏", "sql": "SELECT 1"}],
    })))
    assert len(out) == 2
    assert out[0]["status"] == "indexed"
    assert out[1]["status"] == "error"
    assert "坏条目上传失败" in out[1]["error"]


def test_ingest_faq_slug_default_id(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    entry = {k: v for k, v in _FAQ.items() if k != "id"}
    out = json.loads(asyncio.run(srv.cmd_ingest_faq({"faqs": [entry]})))
    assert out[0]["id"].startswith("各区域销售额排名")
    assert fake.last_upload[0].startswith("faq-各区域销售额排名")


def test_search_faq_hits(monkeypatch):
    fake = FakeClient(retrieve={"items": [{"chunk_id": "c1", "document_id": "d1",
                                          "text": "# 退货率…", "title": "退货率", "score": 0.8}],
                                 "degraded": False})
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_search_faq({"query": "退货率", "top_k": 3})))
    assert out["matches"][0]["score"] == 0.8
    assert out["degraded"] is False


def test_search_faq_empty(monkeypatch):
    fake = FakeClient(retrieve={"items": [], "degraded": False})
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = asyncio.run(srv.cmd_search_faq({"query": "不存在", "top_k": 3}))
    assert "FAQ 无匹配" in out


def test_search_faq_missing_query(monkeypatch):
    out = asyncio.run(srv.cmd_search_faq({}))
    assert "缺少必填参数 query" in out


def test_search_faq_bad_top_k(monkeypatch):
    out = asyncio.run(srv.cmd_search_faq({"query": "q", "top_k": "x"}))
    assert "top_k 应为整数" in out


def test_list_faq_docs(monkeypatch):
    fake = FakeClient(docs=[{"document_id": "d1", "filename": "faq-faq-001.md",
                             "status": "indexed", "kb_id": "kb-faq", "chunk_count": 1}])
    monkeypatch.setattr(srv, "RagentClient", lambda: fake)
    out = json.loads(asyncio.run(srv.cmd_list_faq_docs({})))
    assert out[0]["filename"].startswith("faq-")


def test_mcp_tool_surface_includes_faq():
    tools = asyncio.run(srv.handle_list_tools())
    names = {t.name for t in tools}
    assert {"ingest_faq", "search_faq", "list_faq_docs"} <= names


def test_cmd_returns_text_when_ragent_url_missing(monkeypatch):
    monkeypatch.delenv("RAGENT_URL", raising=False)

    class NoUrlClient(FakeClient):
        def __init__(self, *args, **kwargs):
            raise RagentClientError("RAGENT_URL 未配置")

    monkeypatch.setattr(srv, "RagentClient", lambda: NoUrlClient())
    for cmd, args in [
        (srv.cmd_ingest_faq, {"faqs": [dict(_FAQ)]}),
        (srv.cmd_search_faq, {"query": "q"}),
        (srv.cmd_list_faq_docs, {}),
    ]:
        out = asyncio.run(cmd(args))
        assert "RAGENT_URL" in out, f"{cmd.__name__} 未透传 RAGENT_URL 错误: {out!r}"