"""跨文档关系测试：关系矩阵构建 + 三通道跳转。"""

from app.core.doc_relation import cross_doc_retriever
from app.store import pgvector_store


def test_global_df_aggregates_from_db(ingest_docs):
    """设计审查 P1-8：global DF 走 SQL 聚合，不再把全量实体拖进内存。"""
    df, total = pgvector_store.get_global_df()
    assert total >= 3
    assert df, "不应为空"
    # 共享实体（如 Transformer）至少出现在 2 个文档
    shared = max(df.values())
    assert shared >= 2


def test_doc_ids_with_any_entity_converges_candidates(ingest_docs):
    """candidate 收敛：只返回与给定实体有重叠的文档。"""
    doc1 = ingest_docs["transformer_basics.md"]
    doc2 = ingest_docs["transformer_pytorch.md"]
    doc3 = ingest_docs["rag_chunking.md"]

    entities = pgvector_store.get_doc_entities_bulk([doc1]).get(doc1)
    assert entities
    terms = [e for e, _ in entities[:5]]

    cands = pgvector_store.get_doc_ids_with_any_entity(terms)
    assert doc1 in cands
    assert doc2 in cands  # 共享术语 → 入选
    assert doc3 not in cands  # 无实体重叠 → 不入选（语料有界）


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
        "QKV 投影如何实现",
        None,
        ["test-kb"],
        initial,
        can_read_all=True,
    )
    assert extras, "跨文档跳转未返回任何补充 chunk"
    extra_docs = {c["document_id"] for c in extras}
    assert doc2 in extra_docs
    assert doc3 not in extra_docs
    initial_ids = {c["chunk_id"] for c in initial}
    assert all(c["chunk_id"] not in initial_ids for c in extras)


async def test_cross_doc_extras_reach_final_results(ingest_docs, monkeypatch):
    """L1 回归：直连结果只有 doc1 时，doc2 只能经 cross-doc 通道进入最终结果。

    旧实现 min(score, max_rrf) 把附加 chunk 压到 RRF 量纲，排序后沉底被截断。
    fake 向量会让所有文档都"直连命中"，故 monkeypatch _collect_results
    把直连结果固定为 doc1，隔离出映射逻辑本身。
    """
    import app.core.retrieval as retrieval_mod
    from app.config import settings as s
    from app.core.retrieval import retrieval_engine
    from app.store import pgvector_store

    doc1 = ingest_docs["transformer_basics.md"]
    doc2 = ingest_docs["transformer_pytorch.md"]
    d1_chunks = pgvector_store.get_chunks_by_document(doc1)[:5]
    assert d1_chunks
    for i, c in enumerate(d1_chunks):
        c["score"] = 0.02 - i * 0.001  # RRF 量级的直连分

    async def fake_collect(
        kb_ids, query_emb, query, user_role_ids, can_read_all, top_k, seen_ids, results, user_id=""
    ):
        for c in d1_chunks:
            seen_ids.add(c["chunk_id"])
            results.append(dict(c))

    monkeypatch.setattr(retrieval_mod, "_collect_results", fake_collect)
    monkeypatch.setattr(s, "mmr_enabled", False)

    results = await retrieval_engine.retrieve("多头注意力", None, can_read_all=True)
    doc_ids = {r.document_id for r in results}
    assert doc2 in doc_ids, "cross-doc 附加 chunk 未能进入最终结果（L1 量纲塌方）"


async def test_channel3_discovers_semantically_related_doc(ingest_docs, monkeypatch):
    """阈值放开到 0 时，channel 3 应能独立发现无词法交集的文档 3。"""
    from app.config import settings as s
    from app.core import doc_relation as dr
    from app.core.doc_relation import cross_doc_retriever
    from app.store import pgvector_store

    monkeypatch.setattr(s, "cross_doc_embedding_threshold", 0.0)
    # 会话内历史文档会占满默认 5 个邻居名额，放开上限以观察发现能力
    monkeypatch.setattr(dr, "_MAX_NEIGHBORS_PER_QUERY", 50)
    doc1 = ingest_docs["transformer_basics.md"]
    doc3 = ingest_docs["rag_chunking.md"]
    initial = pgvector_store.get_chunks_by_document(doc1)[:2]
    # 查询词与文档 3 无词法交集 → channel 1/2 不会发现它，只有 channel 3 能
    extras = await cross_doc_retriever.retrieve(
        "缩放点积公式推导",
        [0.1] * 4096,
        ["test-kb"],
        initial,
        can_read_all=True,
    )
    extra_docs = {c["document_id"] for c in extras}
    assert doc3 in extra_docs, "channel 3 未能独立发现语义相关文档"
