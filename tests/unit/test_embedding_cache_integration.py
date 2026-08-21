"""SFEmbedding.embed / embed_single_chunk 与 EmbeddingCache 集成（Day 1 上午）。

锁定：
- 命中 cache 时**不**消耗 rate limit token，不触发熔断计数
- 第二次同文本调用**不**触达 API
- settings.embedding_cache_enabled=False 时 cache 完全旁路（双向）
- 嵌入单条路径同样命中 cache

mock 思路：直接 monkeypatch sf._client + sf._client_loop_id 替换 client.property
返回，避开 AsyncOpenAI 真实调用（conftest 已 sentinel 化 key）。
"""

from app.config import settings
from app.core import cache as cache_mod
from app.llm import embedding as emb_mod


class _FakeEmbeddingsAPI:
    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp([[0.1, 0.2, 0.3]])


class _FakeClient:
    def __init__(self):
        self.embeddings = _FakeEmbeddingsAPI()


class _FakeResp:
    def __init__(self, data):
        self.data = [type("Row", (), {"embedding": v})() for v in data]


def _wire_fake_client(sf: emb_mod.SFEmbedding, monkeypatch) -> _FakeClient:
    """把 SFEmbedding.client property 替换成 FakeClient，避免真实 API。

    直接改 sf._client 会被 property 重建覆盖（property 每次检测 loop id），
    所以必须 monkeypatch 类级 property。
    """
    fake = _FakeClient()
    monkeypatch.setattr(type(sf), "client", property(lambda self: fake))
    return fake


def _reset_cache():
    cache_mod.embedding_cache.clear()


async def test_embed_first_call_populates_cache(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    sf = emb_mod.SFEmbedding()
    fake = _wire_fake_client(sf, monkeypatch)

    v = await sf.embed("hello")
    assert v == [0.1, 0.2, 0.3]
    assert len(fake.embeddings.calls) == 1
    # cache 应已被填充
    assert cache_mod.embedding_cache.get("hello") == [0.1, 0.2, 0.3]


async def test_embed_second_call_hits_cache_no_api(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    sf = emb_mod.SFEmbedding()
    fake = _wire_fake_client(sf, monkeypatch)

    v1 = await sf.embed("repeat")
    v2 = await sf.embed("repeat")
    assert v1 == v2 == [0.1, 0.2, 0.3]
    assert len(fake.embeddings.calls) == 1, "第二次同文本必须走 cache，不应触 API"


async def test_embed_different_text_does_not_share_cache(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    sf = emb_mod.SFEmbedding()
    fake = _wire_fake_client(sf, monkeypatch)

    await sf.embed("alpha")
    await sf.embed("beta")
    assert len(fake.embeddings.calls) == 2, "不同文本不命中彼此 cache"


async def test_embed_cache_disabled_bypasses_completely(monkeypatch):
    """settings.embedding_cache_enabled=False 时两次同文本都应触 API。"""
    _reset_cache()
    monkeypatch.setattr(settings, "embedding_cache_enabled", False)
    sf = emb_mod.SFEmbedding()
    fake = _wire_fake_client(sf, monkeypatch)

    await sf.embed("repeat")
    await sf.embed("repeat")
    assert len(fake.embeddings.calls) == 2, "cache 关时同文本也应触 API"


async def test_embed_cache_disabled_does_not_pollute_cache(monkeypatch):
    """cache 关时调用结果不写入 cache，避免后续误命中。"""
    _reset_cache()
    monkeypatch.setattr(settings, "embedding_cache_enabled", False)
    sf = emb_mod.SFEmbedding()
    _wire_fake_client(sf, monkeypatch)

    await sf.embed("never-cached")
    assert cache_mod.embedding_cache.get("never-cached") is None


async def test_embed_cache_hit_skips_rate_limiter(monkeypatch):
    """命中 cache 的调用不应消耗 rate limit token。"""
    _reset_cache()
    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    sf = emb_mod.SFEmbedding()
    _wire_fake_client(sf, monkeypatch)

    acquire_calls = {"n": 0}
    orig_acquire = sf.limiter.acquire

    async def counting_acquire():
        acquire_calls["n"] += 1
        await orig_acquire()

    sf.limiter.acquire = counting_acquire

    await sf.embed("once")  # miss → 消耗 1
    await sf.embed("once")  # hit → 不消耗
    assert acquire_calls["n"] == 1, "cache hit 必须跳过 rate limiter"


async def test_embed_single_chunk_also_uses_cache(monkeypatch):
    """单条路径（embed_single_chunk）也必须读/写同一个 cache。"""
    _reset_cache()
    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    sf = emb_mod.SFEmbedding()
    fake = _wire_fake_client(sf, monkeypatch)

    v1, err1 = await sf.embed_single_chunk("chunk-text")
    v2, err2 = await sf.embed_single_chunk("chunk-text")
    assert err1 is None and err2 is None
    assert v1 == v2 == [0.1, 0.2, 0.3]
    assert len(fake.embeddings.calls) == 1
