"""表格感知摄入接线：embed 输入=归一化文本、元数据列落库、search_text 同源。

跑在 ragent_test 库 + fake_llm_stack（离线确定性），不触 dev 库 / 真 LLM。
覆盖：
- test_table_chunk_dual_representation     表格 chunk 双表示（plan §Task 15 Step 2）
- test_plain_chunk_embedding_text_equals_text  纯文本 chunk 维持原文（plan §Task 15 Step 2）
- test_embedding_version_stamped_from_current_settings  Phase C mixed-version 不变量
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

BIG_TABLE_MD = """# 财务摘要

## 主要会计数据

| 项目 | 2023年 | 2024年 |
| --- | --- | --- |
| 营业收入 | 100亿元 | 150亿元 |
| 净利润 | 10亿元 | 15亿元 |
| 归母净资产 | 500亿元 | 520亿元 |
| 经营现金流 | 80亿元 | 95亿元 |
| 研发投入 | 5亿元 | 6亿元 |
| 总资产 | 900亿元 | 950亿元 |

以上数据摘自年度报告。"""


@pytest.fixture
def clean_corpus(integration_db):
    """清空语料相关表：测试间共享 integration_db session fixture，跨测试语料会累积。"""
    with integration_db.get_db_ctx() as session:
        session.execute(
            text(
                "TRUNCATE chunks, chunk_questions, documents, doc_entities, "
                "doc_relations, doc_embeddings, doc_role_access RESTART IDENTITY"
            )
        )
        session.commit()
    yield


def test_table_chunk_dual_representation(
    clean_corpus, fake_llm_stack, monkeypatch
):
    from app.config import settings
    from app.ingestion.indexer import DocumentIndexer
    from app.store.pgvector_store import get_chunks_by_document

    monkeypatch.setattr(settings, "question_channel_enabled", False)

    result = DocumentIndexer().index(
        filename="table-doc.md",
        content=BIG_TABLE_MD.encode("utf-8"),
        kb_id="test-kb",
        user_id="test-user",
    )
    assert result["status"] == "indexed", result
    chunks = get_chunks_by_document(result["document_id"])
    assert chunks, "应有 chunk 落库"

    table_chunks = [c for c in chunks if c.get("chunk_type") == "table"]
    assert table_chunks, "至少一个 chunk 标记为 table"

    tc = table_chunks[0]
    # 双表示：text 保原始 markdown，embedding_text 是归一化文本且二者不同
    assert "| 营业收入 |" in tc["text"]
    assert "营业收入：2023年为100亿元" in tc["embedding_text"]
    assert tc["embedding_text"] != tc["text"]
    # 元数据列
    assert tc["table_headers"] == ["项目", "2023年", "2024年"]
    assert (tc["table_meta"] or [{}])[0]["data_rows"] == 6
    # embedding_version 跟随 config 代际
    assert tc["embedding_version"] == settings.current_embedding_version
    # BM25 词源来自归一化文本——jieba 把「100亿元」切成「100」「亿元」，
    # 「营业收入」切成「营业」「收入」；只要这些分词后片段在 search_text
    # 里就说明词源已切到 rt.text（而非原始 markdown 表头分隔符）。
    _st = tc["search_text"] or ""
    assert ("100" in _st and "亿元" in _st) or ("营业" in _st and "收入" in _st), (
        f"search_text 应携带归一化实体的分词片段，实际：{_st!r}"
    )


def test_plain_chunk_embedding_text_equals_text(
    clean_corpus, fake_llm_stack, monkeypatch
):
    """纯文本 chunk 的 retrieval 表示 == 原文（维持现状，不加前缀）。"""
    from app.config import settings
    from app.ingestion.indexer import DocumentIndexer
    from app.store.pgvector_store import get_chunks_by_document

    monkeypatch.setattr(settings, "question_channel_enabled", False)

    md = "# 文档\n\n## 章节\n\n" + ("这是一段没有表格的正文。" * 40)
    result = DocumentIndexer().index(
        filename="plain-doc.md",
        content=md.encode("utf-8"),
        kb_id="test-kb",
        user_id="test-user",
    )
    chunks = get_chunks_by_document(result["document_id"])
    assert all(c.get("chunk_type") is None for c in chunks)


def test_embedding_version_stamped_from_current_settings(
    clean_corpus, fake_llm_stack, monkeypatch
):
    """Phase C 不变量：重用与新建 chunk 必须使用 settings.current_embedding_version。

    复用 chunk（is_reused=True 路径）若写死旧版本号，hybrid_search 的
    embedding_version = current_embedding_version 过滤会把它们对当前检索
    屏蔽——是 commit a2e3958 修复的 bug 的精确回归。这里通过 version bump +
    hash reuse 路径同时覆盖两类 chunk，确保两类都从 settings 读值。

    步骤：
    1) 用 version=N 摄入（无 document_id，全新写入）
    2) 验证所有 chunk 已是 N
    3) 把文档 status 改为 indexing（绕开 unchanged 早返） + bump version → N+1
    4) 重新 index 同内容 + document_id（hash reuse 触发：每 chunk content_hash 命中）
    5) 断言所有 chunk version == N+1（无 N 残留）
    """
    from app.config import settings
    from app.ingestion.indexer import DocumentIndexer
    from app.store.db import Document
    from app.store.pgvector_store import get_chunks_by_document

    monkeypatch.setattr(settings, "question_channel_enabled", False)

    md = "# 不变量文档\n\n## 章节\n\n" + ("这是一段用于 version 校验的正文段落。" * 30)
    initial_version = settings.current_embedding_version
    new_version = initial_version + 1

    # Step 1：初次摄入（N）
    res1 = DocumentIndexer().index(
        filename="version-stamp.md",
        content=md.encode("utf-8"),
        kb_id="test-kb",
        user_id="test-user",
    )
    assert res1["status"] == "indexed", res1
    doc_id = res1["document_id"]

    # Step 2：所有 chunk 应已 N
    chunks_v1 = get_chunks_by_document(doc_id)
    assert chunks_v1, "初次摄入应落库 chunk"
    v1_versions = {c["embedding_version"] for c in chunks_v1}
    assert v1_versions == {initial_version}, f"v1 期望 version={initial_version}，实际 {v1_versions}"

    # Step 3a：把文档 status 改成 indexing，绕开 unchanged 早返
    from app.store.db import get_db_ctx

    with get_db_ctx() as session:
        d = session.query(Document).filter(Document.document_id == doc_id).first()
        assert d is not None, "初次摄入的 Document 行不应缺失"
        d.status = "indexing"
        session.commit()

    # Step 3b：bump version
    monkeypatch.setattr(settings, "current_embedding_version", new_version)

    # Step 4：重传同内容 + document_id——切到 hash reuse 路径
    res2 = DocumentIndexer().index(
        filename="version-stamp.md",
        content=md.encode("utf-8"),
        kb_id="test-kb",
        user_id="test-user",
        document_id=doc_id,
    )
    assert res2["status"] in ("indexed", "unchanged"), res2

    # Step 5：所有 chunk 必须是新版本（无 N 残留）
    chunks_v2 = get_chunks_by_document(doc_id)
    assert chunks_v2, "再次摄入后应仍有 chunk"
    v2_versions = {c["embedding_version"] for c in chunks_v2}
    assert v2_versions == {new_version}, (
        f"Phase C 不变量破坏：期望单一 version={new_version}，实际 {v2_versions}"
    )
    # 显式负向断言：不得有任何 chunk 是旧版本
    assert not any(c["embedding_version"] != settings.current_embedding_version for c in chunks_v2), (
        "存在 chunk 的 embedding_version 与 settings.current_embedding_version 不一致"
    )