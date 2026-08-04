"""锁定 SFEmbedding.embed_with_fallback 分片策略。

旧实现把全部文本塞进单个 batch 请求——大文档超出 SiliconFlow 单请求上限
后整单失败，静默退化为逐条调用（慢一个量级）。现按 32 条分片，
失败批只退化该批。
"""
from app.llm import embedding as emb_mod


async def test_batches_split_by_32(monkeypatch):
    sf = emb_mod.SFEmbedding()
    sizes = []

    async def fake_batch(self, texts, attempt=0):
        sizes.append(len(texts))
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(emb_mod, "_try_batch_with_retry", fake_batch)
    texts = ["t%d" % i for i in range(70)]
    results = await sf.embed_with_fallback(texts)
    assert sizes == [32, 32, 6], "必须按 32 条分片"
    assert len(results) == 70
    assert all(emb is not None and err is None for emb, err in results)


async def test_failed_batch_falls_back_only_for_that_batch(monkeypatch):
    sf = emb_mod.SFEmbedding()

    async def fake_batch(self, texts, attempt=0):
        if texts and texts[0].startswith("bad"):
            return None                      # 该批整批失败
        return [[0.2, 0.2] for _ in texts]

    singles = []

    async def fake_single(self, text, attempt=0):
        singles.append(text)
        return ([0.3, 0.3], None)

    monkeypatch.setattr(emb_mod, "_try_batch_with_retry", fake_batch)
    monkeypatch.setattr(emb_mod.SFEmbedding, "embed_single_chunk", fake_single)

    texts = ["ok%d" % i for i in range(32)] + ["bad%d" % i for i in range(3)]
    results = await sf.embed_with_fallback(texts)
    assert singles == ["bad0", "bad1", "bad2"], "只有失败批退化为逐条"
    assert len(results) == 35
    assert all(emb is not None for emb, _ in results)


async def test_empty_input_returns_empty():
    sf = emb_mod.SFEmbedding()
    assert await sf.embed_with_fallback([]) == []
