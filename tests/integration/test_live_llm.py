"""真实 LLM API 冒烟：仅当 RAGENT_LIVE_LLM=1 且 .env 有真实 key 时运行。"""
import os

import pytest

from app.config import settings

pytestmark = pytest.mark.live_llm


@pytest.fixture
def live_env(monkeypatch):
    if os.environ.get("RAGENT_LIVE_LLM") != "1":
        pytest.skip("未设置 RAGENT_LIVE_LLM=1，跳过真实 API 冒烟")
    from dotenv import dotenv_values
    vals = dotenv_values(".env")
    mm_key = (vals.get("MINIMAX_API_KEY") or "").strip()
    sf_key = (vals.get("SILICONFLOW_API_KEY") or "").strip()
    if not mm_key or not sf_key:
        pytest.skip(".env 缺少 MINIMAX_API_KEY / SILICONFLOW_API_KEY")
    monkeypatch.setattr(settings, "minimax_api_key", mm_key)
    monkeypatch.setattr(settings, "siliconflow_api_key", sf_key)
    # 强制按新 key 重建底层 client（client 属性按 loop 缓存，持有旧 key）
    from app.llm.chat import minimax_client
    from app.llm.embedding import sf_embedding
    for client in (minimax_client, sf_embedding):
        monkeypatch.setattr(client, "_client", None, raising=False)
        monkeypatch.setattr(client, "_client_loop_id", None, raising=False)
    yield


async def test_live_embedding_dimension(live_env):
    from app.llm.embedding import sf_embedding
    vec = await sf_embedding.embed("什么是 Transformer")
    assert isinstance(vec, list)
    assert len(vec) == settings.embedding_dimension


async def test_live_chat_returns_text(live_env):
    from app.llm.chat import minimax_client
    out = await minimax_client.chat(
        [{"role": "user", "content": "只回复两个字：收到"}], max_tokens=16, timeout=30,
    )
    assert out.strip()
