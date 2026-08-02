"""摄入链路测试（真实 PG + fake embedding/metadata）：建块、入库、增量复用。"""
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "docs"


def test_ingest_creates_chunks_questions_and_relations(ingest_docs, integration_db):
    from app.store import pgvector_store
    from app.store.db import get_db_ctx, ChunkQuestion

    doc_id = ingest_docs["transformer_basics.md"]
    chunks = pgvector_store.get_chunks_by_document(doc_id)
    assert len(chunks) >= 3                       # 至少 4 个 H3 小节
    for c in chunks:
        assert c["embedding"] is not None         # fake 向量已落库
        assert c["search_text"]                   # BM25 分词已生成
        assert len(c["embedding"]) == 4096

    with get_db_ctx() as session:
        q_count = session.query(ChunkQuestion).filter(
            ChunkQuestion.chunk_id.like(doc_id + "_%")
        ).count()
    assert q_count > 0                            # fake metadata 的 questions 已入库


def test_reingest_same_content_is_unchanged(ingest_docs):
    from app.ingestion.indexer import document_indexer

    name = "rag_chunking.md"
    res = document_indexer.index(
        name, (FIXTURE_DIR / name).read_bytes(),
        kb_id="test-kb", user_id="test-user",
        document_id=ingest_docs[name],
    )
    assert res["status"] == "unchanged"


def test_incremental_update_reuses_unchanged_chunks(ingest_docs, fake_llm_stack):
    from app.ingestion.indexer import document_indexer

    name = "rag_chunking.md"
    original = (FIXTURE_DIR / name).read_bytes()
    modified = original + "\n\n### 新增小节\n\n这是追加的语义分块实验内容，用于触发增量更新。\n".encode("utf-8")

    fake_llm_stack["embed_with_fallback"].clear()
    res = document_indexer.index(
        name, modified, kb_id="test-kb", user_id="test-user",
        document_id=ingest_docs[name],
    )
    assert res["status"] in ("indexed", "partial")
    # 增量复用：本轮送 embed 的文本数 < 文档总块数（旧块按 content_hash 复用）
    embedded_texts = sum(len(batch) for batch in fake_llm_stack["embed_with_fallback"])
    assert 0 < embedded_texts < res["chunk_count"] + 2   # +2: questions 批次


def _precreate_document_row(filename: str) -> str:
    """模拟 api/documents.py 的上传契约：先建 Document 行，返回 doc_id。"""
    from app.store.db import get_db_ctx, Document, new_id, utc_now

    doc_id = new_id()
    with get_db_ctx() as session:
        session.add(Document(
            document_id=doc_id, kb_id="test-kb", filename=filename,
            owner_id="test-user", status="processing",
            created_at=utc_now(), updated_at=utc_now(),
        ))
        session.commit()
    return doc_id


def test_ingest_masks_pii_before_persist(integration_db, fake_llm_stack):
    """cn_id_card 默认策略为 mask(partial)：脱敏后入库，原文号码不得落库。"""
    from app.ingestion.indexer import document_indexer
    from app.store import pgvector_store

    # 18 位身份证号（公开测试号，过 mod-11 校验）。
    # 注意措辞不能含"测试/示例"——那是 cn_id_card 的上下文排除词，会令规则跳过。
    evil = "# 上传说明\n\n联系人证件号 11010519491231002X 请核查。\n" * 3
    doc_id = _precreate_document_row("pii.md")
    res = document_indexer.index("pii.md", evil.encode("utf-8"),
                                 kb_id="test-kb", user_id="test-user",
                                 document_id=doc_id)
    assert res["status"] == "indexed"
    chunks = pgvector_store.get_chunks_by_document(doc_id)
    assert chunks
    for c in chunks:
        assert "11010519491231002X" not in c["text"]
        assert "110***********002X" in c["text"]


@pytest.mark.xfail(
    reason="已知 bug：indexer 以 document_id=None 调用时 add_chunks 先于 _save_document 执行，"
           "FK schema 下直接违反 chunks_document_id_fkey；生产靠 API 层预建行规避；"
           "待 ingestion-correctness plan 修复",
    strict=False,
)
def test_index_without_precreated_document_row(integration_db, fake_llm_stack):
    """期望：indexer 自身能在 chunks 之前建好 Document 行。当前返回 failed。"""
    from app.ingestion.indexer import document_indexer

    res = document_indexer.index(
        "standalone.md",
        "# 独立摄入\n\n这段内容用于验证无预建 Document 行的摄入路径。\n".encode("utf-8"),
        kb_id="test-kb", user_id="test-user",
    )
    assert res["status"] == "indexed"
