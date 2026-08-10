"""跨进程 ragent-py token 共享缓存 + RagentClient 接入测试。"""
import asyncio
import time

import pytest

from mcp_server import token_cache
from mcp_server.client import RagentClient


@pytest.fixture
def iso_cache(tmp_path):
    """token_cache._PATH 已被 conftest autouse 隔离到本测试 tmp；这里只给路径。"""
    return str(tmp_path / "ragent_token_cache.json")


def test_set_get_roundtrip(iso_cache):
    token_cache.set_token("http://x", "tok")
    assert token_cache.get_token("http://x") == "tok"


def test_expired_returns_none(iso_cache):
    import json
    with open(iso_cache, "w", encoding="utf-8") as f:
        json.dump({"http://x": {"token": "tok", "expires_at": time.time() - 10}}, f)
    assert token_cache.get_token("http://x") is None


def test_invalidate_clears(iso_cache):
    token_cache.set_token("http://x", "tok")
    token_cache.invalidate("http://x")
    assert token_cache.get_token("http://x") is None


def test_ragent_client_login_reuses_shared_cache(iso_cache):
    token_cache.set_token("http://fake", "cached-token")

    async def run():
        # 不设 _http：命中缓存则 _login 不会触碰 HTTP；若误走 HTTP 会 AttributeError
        c = RagentClient(base_url="http://fake", username="admin", password="admin123")
        try:
            await c._login()
            assert c._token == "cached-token"
        finally:
            await c.aclose()

    asyncio.run(run())


def test_ragent_client_login_real_when_cache_empty(iso_cache, monkeypatch):
    import httpx

    async def run():
        c = RagentClient(base_url="http://fake2", username="admin", password="admin123")
        c._http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"access_token": "fresh"}),
        ), base_url="http://fake2")
        try:
            await c._login()
            assert c._token == "fresh"
            assert token_cache.get_token("http://fake2") == "fresh"  # 登录后写缓存
        finally:
            await c.aclose()

    asyncio.run(run())