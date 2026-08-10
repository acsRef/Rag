"""测试套件共享配置。

unit 测试不得触碰真实 DB / 网络 / LLM：
在 import app 包之前用环境变量把凭据换成哨兵值
（pydantic-settings 中环境变量优先于 .env），
app.store.db import 期创建的 engine 会绑定到不存在的地址，
任何误触真实服务的 unit 测试将立即失败。
"""
import os

os.environ["DATABASE_URL"] = "postgresql://test:test@127.0.0.1:1/ragent_test_nonexistent"
os.environ["MINIMAX_API_KEY"] = "test-not-real"
os.environ["SILICONFLOW_API_KEY"] = "test-not-real"
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("PII_ENCRYPTION_KEY", "test-pii-key")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def block_external_services(monkeypatch):
    """运行期二层护栏：运行期读取这些配置的代码路径也拿到哨兵值。

    RAGENT_LIVE_LLM=1 时让路——live 测试需要真实 key（由 live_env 注入），
    否则本 autouse 会把真实 key 又 patch 回哨兵（曾导致 live 全线 401）。
    """
    if os.environ.get("RAGENT_LIVE_LLM") == "1":
        yield
        return
    monkeypatch.setattr(settings, "minimax_api_key", "test-not-real")
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-not-real")
    yield


@pytest.fixture(autouse=True)
def isolate_token_cache(tmp_path, monkeypatch):
    """token 共享缓存落到临时文件，避免测试登录污染真实 ~/.ragent_token_cache.json。"""
    from mcp_server import token_cache

    monkeypatch.setattr(token_cache, "_PATH", str(tmp_path / "ragent_token_cache.json"))
    yield