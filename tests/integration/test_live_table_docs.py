"""多文档复杂表格语料：真实 embedding + 真实 metadata 的摄入/关系/检索验证。

语料为 tests/fixtures/docs-tables/ 下 5 份表格密集文档（产品线×季度明细、
渠道拆分、成本毛利、集团 KPI、渠道手册），互相之间通过产品名/区域名/
渠道名构成交叉关系。仅当 RAGENT_LIVE_LLM=1 且 key 齐备时运行。
"""
from pathlib import Path

import pytest

from app.config import settings

pytestmark = pytest.mark.live_llm

TABLE_DIR = Path(__file__).parent.parent / "fixtures" / "docs-tables"
DOC_FILES = (
    "sales_east_2024.md",
    "sales_south_2024.md",
    "product_cost_margin.md",
    "kpi_summary_2024.md",
    "channel_team_handbook.md",
)


@pytest.fixture(scope="module")
def table_corpus(integration_db, live_env, clean_corpus):
    """摄入 5 份表格文档（真实 embedding/metadata），返回 {filename: doc_id}。"""
    from app.ingestion.indexer import document_indexer

    ids = {}
    for name in DOC_FILES:
        res = document_indexer.index(
            name, (TABLE_DIR / name).read_bytes(),
            kb_id="test-kb", user_id="test-user",
        )
        assert res["status"] == "indexed", "摄入 %s 失败: %s" % (name, res)
        ids[name] = res["document_id"]
    return ids


async def test_table_docs_ingest_structure(table_corpus):
    """表格密集文档应切出足量 chunk，且 embedding 全部落库。"""
    from app.store import pgvector_store

    for name, doc_id in table_corpus.items():
        chunks = pgvector_store.get_chunks_by_document(doc_id)
        assert len(chunks) >= 3, "%s 仅切出 %d 块" % (name, len(chunks))
        assert all(c["embedding"] is not None for c in chunks), name
        assert all(len(c["embedding"]) == settings.embedding_dimension for c in chunks)


async def test_cross_doc_relations_built(table_corpus):
    """共享产品名的文档之间应建立关系边（TF-IDF 互补）。"""
    from app.store import pgvector_store

    east = table_corpus["sales_east_2024.md"]
    rels = pgvector_store.get_doc_relations(east)
    targets = {r["target_doc"] for r in rels}
    candidates = {
        table_corpus["product_cost_margin.md"],
        table_corpus["sales_south_2024.md"],
        table_corpus["kpi_summary_2024.md"],
    }
    assert targets & candidates, "华东文档与任何产品/区域文档都没有关系边"


async def test_retrieval_region_specific_query(table_corpus):
    """区域特化查询应把对应区域文档排进 top-3。"""
    from app.core.retrieval import retrieval_engine

    results = await retrieval_engine.retrieve(
        "智享家Pro 2024年Q3 华东区销售额", None, can_read_all=True)
    assert results
    top_docs = [r.document_id for r in results[:3]]
    assert table_corpus["sales_east_2024.md"] in top_docs


async def test_retrieval_cost_query_hits_cost_doc(table_corpus):
    """成本毛利类查询应命中成本文档。"""
    from app.core.retrieval import retrieval_engine

    results = await retrieval_engine.retrieve(
        "智享家Pro 的毛利率和单位成本是多少", None, can_read_all=True)
    assert results
    top_docs = [r.document_id for r in results[:3]]
    assert table_corpus["product_cost_margin.md"] in top_docs


async def test_retrieval_comparison_spans_regions(table_corpus):
    """跨区域对比查询的结果应横跨至少 2 个区域文档。"""
    from app.core.retrieval import retrieval_engine

    results = await retrieval_engine.retrieve(
        "华东区和华南区 智享家Pro 销售额对比", None, can_read_all=True)
    assert results
    top_doc_set = {r.document_id for r in results[:6]}
    region_docs = {
        table_corpus["sales_east_2024.md"],
        table_corpus["sales_south_2024.md"],
    }
    assert len(top_doc_set & region_docs) >= 2, "对比查询未能横跨两个区域文档"


async def test_mmr_per_doc_soft_cap(table_corpus):
    """MMR 软约束抽查：单文档 chunk 不应霸榜（≤3）。"""
    from collections import Counter
    from app.core.retrieval import retrieval_engine

    results = await retrieval_engine.retrieve(
        "2024 年各产品线销售额与毛利率", None, can_read_all=True)
    counts = Counter(r.document_id for r in results)
    assert counts.most_common(1)[0][1] <= 3
