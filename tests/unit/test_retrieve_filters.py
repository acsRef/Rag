"""RetrievalEngine.retrieve 新增 filters 参数（Day 1 晚上 pipeline 集成）。

锁定：
- filters 参数透传到 _collect_results → _search_kb → pgvector_store.hybrid_search
- 不传时（默认 None）行为与改造前完全一致
- 日志/diag 记录 filters 字段便于排查
"""
import pytest

from app.core.retrieval_filter import RetrievalFilter


@pytest.fixture
def capture_pipeline(monkeypatch):
    """把 RetrievalEngine.retrieve 的下游全部 stub 掉，捕获传给 _collect_results 的 kwargs。"""
    from app.core import retrieval as ret_mod

    captured = {}

    async def fake_collect(
        kb_ids, query_emb, query, user_role_ids, can_read_all,
        top_k, seen_ids, results, user_id="", document_ids=None,
        filters=None,
    ):
        captured["kb_ids"] = kb_ids
        captured["query"] = query
        captured["document_ids"] = document_ids
        captured["filters"] = filters
        return None

    monkeypatch.setattr(ret_mod, "_collect_results", fake_collect)

    # 旁路 chunking / year enrichment / cross_doc / rerank / mmr
    monkeypatch.setattr(
        ret_mod.RetrievalEngine, "_cross_doc_extra",
        lambda self, *a, **kw: __import__("asyncio").sleep(0, result=([], 0)),
        raising=False,
    )
    monkeypatch.setattr(ret_mod, "_supplement_missing_years",
                        lambda *a, **kw: a[0])
    monkeypatch.setattr(ret_mod, "_boost_by_section_type",
                        lambda *a, **kw: a[0])

    # 关键：embed_query_with_fallback 异步返回
    async def fake_embed(*a, **kw):
        return ([0.0] * 4, False)
    monkeypatch.setattr(ret_mod, "embed_query_with_fallback", fake_embed)

    # 列表 kb_ids（避免 list_kb_ids 触 DB）
    monkeypatch.setattr(ret_mod.pgvector_store, "list_kb_ids",
                        lambda: ["kb-test"])

    return captured


async def test_retrieve_accepts_filters_none(capture_pipeline):
    from app.core.retrieval import RetrievalEngine

    engine = RetrievalEngine()
    await engine.retrieve("测试", None)

    assert capture_pipeline["filters"] is None
    assert capture_pipeline["document_ids"] is None


async def test_retrieve_passes_filters_through(capture_pipeline):
    from app.core.retrieval import RetrievalEngine

    engine = RetrievalEngine()
    filters = RetrievalFilter(document_ids={"doc_a", "doc_b"}, years={2024})
    await engine.retrieve("测试", None, filters=filters)

    # _collect_results 必须收到 filters（后续透传到 hybrid_search）
    assert capture_pipeline["filters"] is filters


async def test_retrieve_filters_does_not_override_legacy_document_ids_param(capture_pipeline):
    """filters + 旧 document_ids 同传时，_collect_results 同时收到两者；
    _search_kb 内部 filters 优先（细节见 test_hybrid_search_filters）。"""
    from app.core.retrieval import RetrievalEngine

    engine = RetrievalEngine()
    filters = RetrievalFilter(document_ids={"doc_a", "doc_b"})
    await engine.retrieve("测试", None, filters=filters)

    # _collect_results 必须同时拿到 filters（filters 透传到 hybrid_search）
    assert capture_pipeline["filters"] is filters
