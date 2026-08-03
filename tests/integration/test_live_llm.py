"""真实 LLM API 冒烟：仅当 RAGENT_LIVE_LLM=1 且 .env 有真实 key 时运行。"""
import os

import pytest

from app.config import settings

pytestmark = pytest.mark.live_llm

# live_env fixture 已提取到 tests/integration/conftest.py 共享


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
