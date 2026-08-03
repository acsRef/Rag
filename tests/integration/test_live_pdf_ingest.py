"""单文档复杂真实 PDF：中国平安 2026 年第一季度报告。

docling 解析（含表格清洗入库）→ 结构断言 → 金融术语检索命中。
首次运行会触发 docling 模型下载（数分钟级）。PDF 不在预期路径则 skip。
仅当 RAGENT_LIVE_LLM=1 且 key 齐备时运行。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_llm

PDF_PATH = Path(
    r"C:\Users\Lenovo\Downloads\中国平安：海外监管公告 - 中国平安保险（集团）股份有限公司2026年第一季度报告.pdf")


@pytest.fixture(scope="module")
def pingan_doc(integration_db, live_env, clean_corpus):
    if not PDF_PATH.exists():
        pytest.skip("平安一季报 PDF 不在预期路径：%s" % PDF_PATH)
    from app.ingestion.indexer import document_indexer

    res = document_indexer.index(
        PDF_PATH.name, PDF_PATH.read_bytes(),
        kb_id="test-kb", user_id="test-user",
    )
    assert res["status"] == "indexed", "PDF 摄入失败: %s" % res
    return res


def test_pdf_ingest_structure(pingan_doc):
    """季度报告（1MB、含大量财务报表表格）应切出足量 chunk。"""
    from app.store import pgvector_store

    chunks = pgvector_store.get_chunks_by_document(pingan_doc["document_id"])
    assert len(chunks) >= 20, "1MB 季报仅切出 %d 块，疑似解析/切分异常" % len(chunks)
    assert all(c["embedding"] is not None for c in chunks)


def test_pdf_table_content_landed(pingan_doc):
    """财务报表词汇应出现在入库 chunk 中（表格清洗后仍可检索）。"""
    from app.store import pgvector_store

    chunks = pgvector_store.get_chunks_by_document(pingan_doc["document_id"])
    joined = " ".join(c["text"] for c in chunks)
    finance_terms = ("营业收入", "净利润", "保险服务收入", "总资产", "股东")
    hits = [t for t in finance_terms if t in joined]
    assert len(hits) >= 2, "财务词汇命中过少（%s），表格内容可能未入库" % (hits,)


async def test_pdf_retrieval_revenue(pingan_doc):
    """营业收入类查询应命中该文档。"""
    from app.core.retrieval import retrieval_engine

    results = await retrieval_engine.retrieve(
        "中国平安2026年第一季度营业收入", None, can_read_all=True)
    assert results
    top_docs = {r.document_id for r in results[:5]}
    assert pingan_doc["document_id"] in top_docs


async def test_pdf_retrieval_profit(pingan_doc):
    """净利润类查询应命中该文档。"""
    from app.core.retrieval import retrieval_engine

    results = await retrieval_engine.retrieve(
        "中国平安2026年第一季度净利润表现", None, can_read_all=True)
    assert results
    top_docs = {r.document_id for r in results[:5]}
    assert pingan_doc["document_id"] in top_docs
