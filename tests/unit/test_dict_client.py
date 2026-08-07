"""RagentClient：登录/401 重登、KB ensure 按名复用、上传、状态轮询、retrieve。

httpx MockTransport 全离线。
"""
import asyncio
import json

import httpx
import pytest

from mcp_server.client import RagentClient, RagentClientError


def _handler(routes: dict):
    def handle(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, json={"detail": "not found"})
        resp = routes[key]
        return resp(request) if callable(resp) else resp
    return handle


def _client(handler) -> RagentClient:
    c = RagentClient(base_url="http://fake", username="admin", password="admin123")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://fake")
    return c


def test_login_failure_message():
    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(401, json={"detail": "bad"}),
        }))
        try:
            with pytest.raises(RagentClientError, match="登录失败"):
                await c.ensure_kb("数据字典")
        finally:
            await c.aclose()
    asyncio.run(run())


def test_ensure_kb_reuses_existing_by_name():
    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
            ("GET", "/api/v1/kb"): httpx.Response(200, json=[{"id": "kb-9", "name": "数据字典"}]),
        }))
        try:
            kb_id = await c.ensure_kb("数据字典")
            assert kb_id == "kb-9"
        finally:
            await c.aclose()
    asyncio.run(run())


def test_ensure_kb_creates_when_absent():
    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
            ("GET", "/api/v1/kb"): httpx.Response(200, json=[]),
            ("POST", "/api/v1/kb"): httpx.Response(200, json={"id": "kb-new", "name": "数据字典"}),
        }))
        try:
            assert await c.ensure_kb("数据字典") == "kb-new"
        finally:
            await c.aclose()
    asyncio.run(run())


def test_upload_then_wait_indexed():
    state = {"calls": 0}

    def status(request):
        state["calls"] += 1
        if state["calls"] < 2:
            return httpx.Response(200, json={"document_id": "d1", "status": "processing", "chunk_count": 0})
        return httpx.Response(200, json={"document_id": "d1", "status": "indexed", "chunk_count": 3})

    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
            ("POST", "/api/v1/documents/upload"): httpx.Response(
                200, json={"document_id": "d1", "filename": "dict-api_x.md", "status": "processing", "chunk_count": 0}),
            ("GET", "/api/v1/documents/d1"): status,
        }))
        try:
            up = await c.upload_document("kb-9", "dict-api_x.md", "# x")
            doc = await c.wait_indexed(up["document_id"], interval_s=0.01)
            assert doc["status"] == "indexed"
            assert doc["chunk_count"] == 3
        finally:
            await c.aclose()
    asyncio.run(run())


def test_retrieve_passes_payload():
    def check(request):
        body = json.loads(request.content)
        assert body["kb_ids"] == ["kb-9"]
        return httpx.Response(200, json={"items": [], "degraded": False})

    async def run():
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
            ("POST", "/api/v1/retrieve"): check,
        }))
        try:
            out = await c.retrieve("销售额", ["kb-9"], top_k=3)
            assert out["items"] == []
        finally:
            await c.aclose()
    asyncio.run(run())


def test_unmatched_route_raises_with_diagnostic():
    """未注册路由时错误信息必须是诊断性的（包含 404 状态码 + 响应体片段）。

    钉死契约：retrieve 命中未注册路径，错误文本应包含 HTTP 状态码与响应体，
    便于上游快速定位（路由表漂移、版本不匹配等）。
    """
    async def run():
        # 路由表只有 login，没有 /retrieve —— 调用 retrieve 会得到 404
        c = _client(_handler({
            ("POST", "/api/v1/auth/login"): httpx.Response(200, json={"access_token": "t"}),
        }))
        try:
            with pytest.raises(RagentClientError) as excinfo:
                await c.retrieve("销售额", ["kb-9"], top_k=3)
            msg = str(excinfo.value)
            # 必须包含响应体（_handler 对未注册路径返回 {"detail": "not found"}）
            assert "not found" in msg, f"错误信息应包含响应体诊断，实际: {msg}"
            # 同时保留 HTTP 状态码以便排查
            assert "404" in msg, f"错误信息应包含 HTTP 状态码，实际: {msg}"
        finally:
            await c.aclose()
    asyncio.run(run())
