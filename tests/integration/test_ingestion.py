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


def test_index_without_precreated_document_row(integration_db, fake_llm_stack):
    """期望：indexer 自身能在 chunks 之前建好 Document 行。当前返回 failed。"""
    from app.ingestion.indexer import document_indexer

    res = document_indexer.index(
        "standalone.md",
        "# 独立摄入\n\n这段内容用于验证无预建 Document 行的摄入路径。\n".encode("utf-8"),
        kb_id="test-kb", user_id="test-user",
    )
    assert res["status"] == "indexed"


def test_failed_doc_can_be_retried(integration_db, fake_llm_stack, monkeypatch):
    """失败文档用相同内容重试必须真正重索引（旧逻辑 unchanged 短路，永远卡死）。"""
    from app.ingestion.indexer import document_indexer
    from app.llm.embedding import sf_embedding

    NL = chr(10)
    doc_id = _precreate_document_row("retry.md")
    content = ("# 重试" + NL + NL + "这是用于重试测试的内容。" + NL).encode("utf-8")

    async def all_fail(texts, **kw):
        return [(None, "模拟失败") for _ in texts]

    monkeypatch.setattr(sf_embedding, "embed_with_fallback", all_fail)
    res1 = document_indexer.index("retry.md", content, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res1["status"] == "failed"

    # 恢复可用的 embedding fake（本测试未依赖 fake_llm_stack 的 embed 路径）
    from tests.integration.conftest import fake_vector

    async def ok_embed(texts, **kw):
        return [(fake_vector(t), None) for t in texts]

    monkeypatch.setattr(sf_embedding, "embed_with_fallback", ok_embed)
    res2 = document_indexer.index("retry.md", content, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res2["status"] == "indexed", "失败文档重试被 unchanged 短路"


def test_questions_align_with_persisted_chunks(integration_db, fake_llm_stack, monkeypatch):
    """单 chunk embedding 失败时，questions 不得挂到别的 chunk（旧 zip 错位）。"""
    from app.ingestion.indexer import document_indexer
    from app.llm.embedding import sf_embedding
    from app.store import pgvector_store
    from app.store.db import ChunkQuestion, get_db_ctx

    async def selective_fail(texts, **kw):
        from tests.integration.conftest import fake_vector
        return [(None, "模拟失败") if "乙段失败标记" in t else (fake_vector(t), None)
                for t in texts]

    monkeypatch.setattr(sf_embedding, "embed_with_fallback", selective_fail)
    doc_id = _precreate_document_row("align.md")
    NL = chr(10)
    md = NL.join([
        "# 对齐测试",
        "### 甲", "甲段内容文字。",
        "### 乙", "乙段失败标记内容。",
        "### 丙", "丙段内容文字。",
    ])
    res = document_indexer.index("align.md", md.encode("utf-8"), kb_id="test-kb",
                                 user_id="test-user", document_id=doc_id)
    assert res["status"] == "partial"

    chunks = {c["chunk_id"]: c["text"] for c in pgvector_store.get_chunks_by_document(doc_id)}
    assert len(chunks) == 2                      # 乙段被跳过
    with get_db_ctx() as session:
        rows = session.query(ChunkQuestion).filter(
            ChunkQuestion.chunk_id.like(doc_id + "%")).all()
    assert rows
    for r in rows:
        assert r.chunk_id in chunks
        marker = "甲" if "甲" in r.question else "丙"
        assert marker in chunks[r.chunk_id], "questions 挂错 chunk（zip 错位）"


def test_all_embed_failed_keeps_old_index(integration_db, fake_llm_stack, monkeypatch):
    """重索引时新 chunk 全部 embedding 失败 → failed 且旧索引原样保留。"""
    from app.ingestion.indexer import document_indexer
    from app.llm.embedding import sf_embedding
    from app.store import pgvector_store

    NL = chr(10)
    doc_id = _precreate_document_row("keepold.md")
    v1 = ("# 保留" + NL + NL + "第一版内容。" + NL).encode("utf-8")
    res1 = document_indexer.index("keepold.md", v1, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res1["status"] == "indexed"
    n_before = len(pgvector_store.get_chunks_by_document(doc_id))

    async def all_fail(texts, **kw):
        return [(None, "模拟失败") for _ in texts]

    monkeypatch.setattr(sf_embedding, "embed_with_fallback", all_fail)
    v2 = ("# 保留" + NL + NL + "第一版内容。" + NL + NL
          + "### 新增" + NL + NL + "第二版新增内容。" + NL).encode("utf-8")
    res2 = document_indexer.index("keepold.md", v2, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res2["status"] == "failed"
    assert len(pgvector_store.get_chunks_by_document(doc_id)) == n_before
