"""跨文档关系测试：关系矩阵构建 + 三通道跳转。"""
import pytest

from app.core.doc_relation import cross_doc_retriever
from app.store import pgvector_store


def test_get_chunks_by_document_includes_document_id(ingest_docs):
    chunks = pgvector_store.get_chunks_by_document(ingest_docs["transformer_basics.md"])
    assert chunks
    assert all(c.get("document_id") for c in chunks)


def test_bulk_chunks_capped_per_doc(ingest_docs):
    """邻居文档 chunk 再多，bulk 每文档也不超过 10 条（防 rerank 被淹没）。"""
    from app.store.pgvector_store import get_chunks_by_documents_bulk

    bulk = get_chunks_by_documents_bulk(list(ingest_docs.values()), can_read_all=True)
    assert bulk
    for doc_id, chunks in bulk.items():
        assert len(chunks) <= 10
        assert all(c.get("document_id") == doc_id for c in chunks)


def test_relation_edge_between_related_docs_only(ingest_docs):
    """文档 1↔2 共享术语 → 有关系边；文档 3 无交集 → 无边。"""
    rels = pgvector_store.get_doc_relations(ingest_docs["transformer_basics.md"])
    targets = {r["target_doc"]: r for r in rels}
    assert ingest_docs["transformer_pytorch.md"] in targets
    assert ingest_docs["rag_chunking.md"] not in targets


async def test_cross_doc_jump_returns_related_doc_chunks(ingest_docs):
    """以文档 1 的 chunk 为初始结果，跳转应带回文档 2 的 chunk，且不含文档 3。"""
    doc1 = ingest_docs["transformer_basics.md"]
    doc2 = ingest_docs["transformer_pytorch.md"]
    doc3 = ingest_docs["rag_chunking.md"]

    initial = pgvector_store.get_chunks_by_document(doc1)[:3]
    assert initial
    # 绕行已知 bug：get_chunks_by_document 缺 document_id 字段（见上方 xfail），
    # 手工补齐，使本测试只聚焦跳转逻辑本身。
    for c in initial:
        c["document_id"] = doc1

    extras = await cross_doc_retriever.retrieve(
        "QKV 投影如何实现", None, ["test-kb"], initial, can_read_all=True,
    )
    assert extras, "跨文档跳转未返回任何补充 chunk"
    extra_docs = {c["document_id"] for c in extras}
    assert doc2 in extra_docs
    assert doc3 not in extra_docs
    initial_ids = {c["chunk_id"] for c in initial}
    assert all(c["chunk_id"] not in initial_ids for c in extras)


@pytest.mark.xfail(
    reason="已知 bug：hybrid 模式下 cross-doc 附加 chunk 被 min(score, max_rrf) 压到 RRF 量纲，"
           "候选截断后沉底，三通道机制失效；待 cross-doc-retrieval-overhaul plan 修复",
    strict=False,
)
async def test_cross_doc_extras_survive_tight_candidate_cut(ingest_docs, monkeypatch):
    from app.config import settings as s
    from app.core.retrieval import retrieval_engine

    monkeypatch.setattr(s, "mmr_enabled", False)
    monkeypatch.setattr(s, "mmr_candidate_k", 2)   # 极小候选窗口放大分数量纲问题
    # 查询用 doc1 独有术语（缩放/点积 只在 transformer_basics 中出现）：
    # doc2 若进最终结果，只能经由 cross-doc 通道——断言才有判别力。
    results = await retrieval_engine.retrieve(
        "缩放点积", None, can_read_all=True,
    )
    doc_ids = {r.document_id for r in results}
    assert ingest_docs["transformer_pytorch.md"] in doc_ids
