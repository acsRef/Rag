"""摄入侧 question 门控：flag off 时不得生成问题向量、不得写 chunk_questions。

零落库设计：DocumentIndexer.index 的全部持久化出口（document 行、chunk 行、
问题行、孤儿清理、chunk diag）全部 stub；只断言 upsert_chunk_questions
是否被调用。
"""

import pytest

from app.config import settings

MD = "# 标题\n\n## 小节\n\n这是正文内容，足够长以便切块。\n\n" + ("详细段落内容。" * 50)


class _Recorder:
    def __init__(self):
        self.upsert_calls: list[list[dict]] = []
        self.question_embed_calls: list[list[str]] = []


def _install_stubs(monkeypatch, rec: _Recorder):
    from app.ingestion import indexer as idx_mod
    from app.llm.embedding import sf_embedding

    def fake_generate(chunks):
        for c in chunks:
            c.title = "标题"
            c.summary = "摘要"
            c.questions = ["生成的测试问题？"]
        return chunks

    # 必须保持 async：indexer 两侧调用点都是 asyncio.run(embed_with_fallback(...))，
    # 同步 stub 会让 asyncio.run 收到普通返回值直接 ValueError。
    # 真实签名返回 list[(embedding|None, err|None)]——stub 必须同形。
    async def counting_embed(texts, *args, **kwargs):
        rec.question_embed_calls.append([str(t) for t in texts])
        return [([0.1] * settings.embedding_dimension, None) for _ in texts]

    monkeypatch.setattr(idx_mod.chunk_metadata_generator, "generate", fake_generate)
    monkeypatch.setattr(sf_embedding, "embed_with_fallback", counting_embed)

    # 落库出口全 stub —— unit 层零 DB
    monkeypatch.setattr(
        idx_mod.pgvector_store,
        "add_chunks",
        lambda chunks_data: None,
    )
    monkeypatch.setattr(
        idx_mod.pgvector_store,
        "replace_chunks",
        lambda document_id, chunks_data: None,
    )
    monkeypatch.setattr(
        idx_mod.pgvector_store,
        "upsert_chunk_questions",
        lambda data: rec.upsert_calls.append(data),
    )
    monkeypatch.setattr(
        idx_mod.pgvector_store,
        "delete_orphan_chunk_questions",
        lambda doc_id, valid_ids: None,
    )
    monkeypatch.setattr(idx_mod.DocumentIndexer, "_save_document", lambda self, *a, **k: "")
    monkeypatch.setattr(idx_mod.DocumentIndexer, "_save_chunk_diag", lambda self, *a, **k: None)
    monkeypatch.setattr(idx_mod, "_emit_progress", lambda *a, **k: None)
    # cross_doc 关系矩阵重建也是落库出口（成功路径末尾必调）
    monkeypatch.setattr(
        idx_mod.cross_doc_builder,
        "update_for_document",
        lambda doc_id: None,
    )


def test_ingest_skips_questions_when_channel_off(monkeypatch):
    """channel off：不生成问题向量、不写 chunk_questions。"""
    from app.ingestion.indexer import DocumentIndexer

    rec = _Recorder()
    _install_stubs(monkeypatch, rec)
    monkeypatch.setattr(settings, "question_channel_enabled", False)

    result = DocumentIndexer().index(
        filename="gating-off.md", content=MD.encode("utf-8"), kb_id="kbx"
    )

    assert result["status"] == "indexed"
    assert rec.upsert_calls == []  # 未写任何问题向量
    assert len(rec.question_embed_calls) == 1  # 只有主 chunk embedding 这一次调用


def test_ingest_stores_questions_when_channel_on(monkeypatch):
    """channel on：问题向量正常落库（现有行为保持）。"""
    from app.ingestion.indexer import DocumentIndexer

    rec = _Recorder()
    _install_stubs(monkeypatch, rec)
    monkeypatch.setattr(settings, "question_channel_enabled", True)

    result = DocumentIndexer().index(
        filename="gating-on.md", content=MD.encode("utf-8"), kb_id="kbx"
    )

    assert result["status"] == "indexed"
    assert len(rec.upsert_calls) >= 1  # 问题向量落库


@pytest.mark.parametrize("flag", [False, True])
def test_orphan_cleanup_runs_regardless_of_flag(monkeypatch, flag):
    """孤儿清理只删无效行，与开关无关——off 时也必须跑（回归守口）。"""
    from app.ingestion import indexer as idx_mod
    from app.ingestion.indexer import DocumentIndexer

    rec = _Recorder()
    _install_stubs(monkeypatch, rec)
    orphan_calls: list[tuple] = []
    monkeypatch.setattr(
        idx_mod.pgvector_store,
        "delete_orphan_chunk_questions",
        lambda doc_id, valid_ids: orphan_calls.append((doc_id, valid_ids)),
    )
    monkeypatch.setattr(settings, "question_channel_enabled", flag)

    result = DocumentIndexer().index(
        filename=f"gating-orphan-{flag}.md", content=MD.encode("utf-8"), kb_id="kbx"
    )

    assert result["status"] == "indexed"
    assert len(orphan_calls) == 1
