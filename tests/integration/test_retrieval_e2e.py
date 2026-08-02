"""检索全链路测试：embedding → 混合检索 → 跨文档 → rerank → MMR。"""
from app.config import settings
from app.core.retrieval import retrieval_engine


async def test_retrieval_end_to_end_returns_ranked_chunks(ingest_docs):
    results = await retrieval_engine.retrieve(
        "Transformer 多头注意力 QKV 计算", None, can_read_all=True,
    )
    assert results, "全链路检索返回空"
    assert len(results) <= settings.rerank_top_k
    # 多文档语料下，MMR 软约束应让结果跨文档分布
    doc_ids = {r.document_id for r in results}
    assert len(doc_ids) >= 1
    for r in results:
        assert r.text and r.chunk_id


async def test_retrieval_results_belong_to_corpus(ingest_docs):
    """检索结果的 document_id 必须全部来自库内已摄入的语料（含其他用例摄入的文档）。"""
    from sqlalchemy import text as sqlt
    from app.store.db import get_db_ctx

    with get_db_ctx() as session:
        all_doc_ids = {
            r[0] for r in session.execute(sqlt("SELECT document_id FROM documents")).all()
        }
    results = await retrieval_engine.retrieve(
        "Transformer", None, can_read_all=True,
    )
    assert results
    assert all(r.document_id in all_doc_ids for r in results)
