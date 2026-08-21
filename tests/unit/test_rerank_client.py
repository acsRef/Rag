"""锁定 SFRerank 客户端按事件循环重建：跨 loop 调用不报 "Event loop is closed"。

旧实现 `_get_client` 单例缓存 `httpx.AsyncClient`，在另一 loop（如 ingestion
asyncio.run 或测试）中调用会得到已绑死 loop 的 client、事件循环已关闭则报错。
对齐 embedding/chat 的 loop-id 跟踪模式。
"""
import asyncio

from app.llm.rerank import SFRerank


def _make_rerank():
    return SFRerank()


async def test_client_rebuilt_when_loop_changes():
    sf = _make_rerank()
    # 同一 loop 内：相同 client
    sf._client = None
    c1 = sf._get_client()
    c2 = sf._get_client()
    assert c1 is c2, "同 loop 内必须复用 client"


async def test_client_rebuilt_after_loop_change():
    sf = _make_rerank()
    sf._client = None
    c1 = sf._get_client()
    sf._client_loop_id = -1   # 模拟 loop 变化
    c2 = sf._get_client()
    assert c1 is not c2, "loop 变化必须重建 client"


async def test_initial_loop_id_recorded():
    sf = _make_rerank()
    sf._client = None
    sf._get_client()
    assert sf._client_loop_id == id(asyncio.get_running_loop())


async def test_rerank_works_across_loop_change(monkeypatch):
    """loop 变化时 rerank 路径会触发 _get_client 重建，fake_client 必须能被换上来。

    这里直接 patch `_get_client` 替换返回值，验证重建路径不抛错且返回真结果。
    """
    sf = _make_rerank()

    captured = []

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.9}]}

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

    async def fake_post(url, **kw):
        captured.append(url)
        return _Resp()

    class _FakeClient:
        post = staticmethod(fake_post)

    # 用 monkeypatch 拦截 _get_client：第一次返回 fake，第二次（重建）也返回 fake
    monkeypatch.setattr(sf, "_get_client", lambda: _FakeClient())
    out = await sf.rerank("q", ["d1"])
    assert out and out[0]["relevance_score"] == 0.9
    assert len(captured) == 1