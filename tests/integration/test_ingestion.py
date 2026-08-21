"""摄入链路测试（真实 PG + fake embedding/metadata）：建块、入库、增量复用。"""
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "docs"


def test_ingest_creates_chunks_questions_and_relations(ingest_docs, integration_db):
    from app.store import pgvector_store
    from app.store.db import ChunkQuestion, get_db_ctx

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
    modified = original + "\n\n### 新增小节\n\n这是追加的语义分块实验内容，用于触发增量更新。\n".encode()

    fake_llm_stack["embed_with_fallback"].clear()
    res = document_indexer.index(
        name, modified, kb_id="test-kb", user_id="test-user",
        document_id=ingest_docs[name],
    )
    assert res["status"] in ("indexed", "partial")
    # 增量复用：本轮送 embed 的文本数 < 文档总块数（旧块按 content_hash 复用）
    embedded_texts = sum(len(batch) for batch in fake_llm_stack["embed_with_fallback"])
    assert 0 < embedded_texts < res["chunk_count"] + 2   # +2: questions 批次


def test_incremental_update_preserves_reused_questions(ingest_docs, fake_llm_stack):
    """增量更新不得丢失复用 chunk 的问题向量。

    旧实现 replace_chunks 全删全插，chunk_questions 外键 CASCADE 把复用 chunk
    的问题行删光，而重建只覆盖新 chunk → question 通道每次增量上传都在损耗。
    """
    from app.ingestion.indexer import document_indexer
    from app.store import pgvector_store
    from app.store.db import ChunkQuestion, get_db_ctx

    name = "rag_chunking.md"
    doc_id = ingest_docs[name]
    original = (FIXTURE_DIR / name).read_bytes()

    def _question_counts() -> dict:
        with get_db_ctx() as session:
            rows = session.query(ChunkQuestion.chunk_id).filter(
                ChunkQuestion.chunk_id.like(doc_id + r"\_%", escape="\\")
            ).all()
        counts: dict = {}
        for (cid,) in rows:
            counts[cid] = counts.get(cid, 0) + 1
        return counts

    counts_before = _question_counts()
    assert counts_before, "首次摄入应已写入问题行"

    modified = original + "\n\n### 追加小节\n\n这是追加的内容，用于触发增量更新路径。\n".encode()
    res = document_indexer.index(
        name, modified, kb_id="test-kb", user_id="test-user",
        document_id=doc_id,
    )
    assert res["status"] in ("indexed", "partial")

    counts_after = _question_counts()
    chunks_after = {c["chunk_id"] for c in pgvector_store.get_chunks_by_document(doc_id)}
    # 存活下来的复用 chunk：问题行原样保留
    for cid, n in counts_before.items():
        if cid in chunks_after:
            assert counts_after.get(cid, 0) == n, (
                "增量更新丢失复用 chunk %s 的问题向量" % cid[:12]
            )
    # 新增 chunk：问题行必须挂上
    new_ids = chunks_after - set(counts_before)
    assert new_ids, "追加小节应产生新 chunk"
    for cid in new_ids:
        assert counts_after.get(cid, 0) > 0, "新 chunk %s 没有问题行" % cid[:12]


def test_neighbor_expansion_returns_context(ingest_docs):
    """上下文扩展：hash chunk id 下 ±N 邻居仍要取到（旧实现按尾部序号解析，已失效）。"""
    from app.store import pgvector_store
    from app.store.db import Chunk, get_db_ctx

    doc_id = ingest_docs["transformer_basics.md"]
    with get_db_ctx() as session:
        rows = (
            session.query(Chunk.chunk_id)
            .filter(Chunk.document_id == doc_id)
            .order_by(Chunk.created_at, Chunk.id)
            .all()
        )
    chunk_ids = [r[0] for r in rows]
    assert len(chunk_ids) >= 3

    mid = len(chunk_ids) // 2
    neighbors = pgvector_store.get_neighbor_chunks([(doc_id, chunk_ids[mid])], expand_n=1)
    assert chunk_ids[mid] in neighbors
    assert neighbors[chunk_ids[mid]]["before"], "锚点前邻居文本为空"
    assert neighbors[chunk_ids[mid]]["after"], "锚点后邻居文本为空"

    # 首块只有 after，末块只有 before
    first = pgvector_store.get_neighbor_chunks([(doc_id, chunk_ids[0])], expand_n=1)
    assert first[chunk_ids[0]]["before"] == ""
    assert first[chunk_ids[0]]["after"]
    last = pgvector_store.get_neighbor_chunks([(doc_id, chunk_ids[-1])], expand_n=1)
    assert last[chunk_ids[-1]]["after"] == ""
    assert last[chunk_ids[-1]]["before"]


def test_neighbor_expansion_survives_incremental_update(ingest_docs, fake_llm_stack):
    """增量更新后 created_at 仍编码逻辑顺序：新块插在中间时邻居关系保持正确。"""
    from app.ingestion.indexer import document_indexer
    from app.store import pgvector_store
    from app.store.db import Chunk, get_db_ctx

    name = "rag_chunking.md"
    doc_id = ingest_docs[name]
    # fixture 是 CRLF 行尾，归一化后再按小节切分（cleaner 摄入时同样会归一化）
    original = (FIXTURE_DIR / name).read_bytes().replace(b"\r\n", b"\n")

    # 在文档中间插入新小节
    marker = b"\n\n### "
    parts = original.split(marker)
    assert len(parts) >= 3, "fixture 应含多个 H3 小节"
    middle = "\n\n### 中间插入小节\n\n这是插在文档中间的邻居扩展验证内容。\n".encode()
    inserted = parts[0] + marker + parts[1] + middle + marker + marker.join(parts[2:])

    res = document_indexer.index(
        name, inserted, kb_id="test-kb", user_id="test-user",
        document_id=doc_id,
    )
    assert res["status"] in ("indexed", "partial")

    with get_db_ctx() as session:
        rows = (
            session.query(Chunk.chunk_id, Chunk.text)
            .filter(Chunk.document_id == doc_id)
            .order_by(Chunk.created_at, Chunk.id)
            .all()
        )
    order = [r.chunk_id for r in rows]
    texts = {r.chunk_id: r.text for r in rows}
    anchor = next(cid for cid in order if "邻居扩展验证内容" in texts[cid])
    idx = order.index(anchor)
    assert 0 < idx < len(order) - 1, "插入的小节应位于文档中间"

    neighbors = pgvector_store.get_neighbor_chunks([(doc_id, anchor)], expand_n=1)
    nb = neighbors[anchor]
    assert nb["before"] == texts[order[idx - 1]]
    assert nb["after"] == texts[order[idx + 1]]


def test_reindex_partial_embed_failure_preserves_old_index(integration_db, fake_llm_stack, monkeypatch):
    """重索引时部分新块 embedding 失败：必须保留旧索引 + status=failed，
    不得让差量 upsert 把失败新块对应的旧行删掉（旧实现下静默丢内容）。

    旧实现只挡「全部新失败」，对部分失败→ 部分旧行被删、新行不入库→
    重试仍能恢复但中间窗口查询缺数据。新行为：任一新块失败 + 旧索引存在→
    整体保留旧索引 + failed，重试复用 hash 即可恢复。
    """
    from app.ingestion.indexer import document_indexer
    from app.llm.embedding import sf_embedding
    from app.store import pgvector_store

    doc_id = _precreate_document_row("keep-partial.md")
    NL = chr(10)
    v1 = "# T1" + NL + NL + "首版正文，唯一小节。" + NL
    res1 = document_indexer.index("keep-partial.md", v1.encode("utf-8"),
                                 kb_id="test-kb", user_id="test-user",
                                 document_id=doc_id)
    assert res1["status"] == "indexed"
    old_chunk_ids = {c["chunk_id"] for c in pgvector_store.get_chunks_by_document(doc_id)}
    assert old_chunk_ids

    # 第二版：新增 + 修改若干小节；让其中一条新块 embedding 失败
    async def selective(texts, **kw):
        from tests.integration.conftest import fake_vector
        out = []
        for t in texts:
            if "FAILS" in t:
                out.append((None, "模拟失败"))
            else:
                out.append((fake_vector(t), None))
        return out

    monkeypatch.setattr(sf_embedding, "embed_with_fallback", selective)

    v2 = (
        "# T2" + NL + NL + "首版正文，唯一小节。" + NL + NL
        + "### 新增小节" + NL + NL + "FAILS" + NL + NL
        + "### 新增正常小节" + NL + NL + "这一节会成功向量化。" + NL
    ).encode("utf-8")
    res2 = document_indexer.index("keep-partial.md", v2, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res2["status"] == "failed"
    assert "保留旧索引" in res2.get("message", "")

    # 旧 chunk 全部完整保留（chunk_id + content 不变）
    after_ids = {c["chunk_id"] for c in pgvector_store.get_chunks_by_document(doc_id)}
    assert after_ids == old_chunk_ids, (
        f"重索引部分失败应保留旧索引；旧={len(old_chunk_ids)} 现={len(after_ids)}")


def _precreate_document_row(filename: str) -> str:
    """模拟 api/documents.py 的上传契约：先建 Document 行，返回 doc_id。"""
    from app.store.db import Document, get_db_ctx, new_id, utc_now

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


def test_content_hash_is_post_pii(integration_db, fake_llm_stack):
    """设计审查 P0-6：content_hash 须基于脱敏后文本，而非原始文本。

    旧实现 `doc_hash = _content_hash(text)` 在 PII mask 之前执行，落库内容已
    脱敏、hash 却基于原文——PII 规则变更后重传被 hash 跳过，垃圾内容永不重索引。
    """
    from app.config import settings
    from app.ingestion.cleaner import document_cleaner
    from app.ingestion.indexer import _content_hash, document_indexer
    from app.ingestion.parser import document_parser
    from app.store.db import Document, get_db_ctx

    evil = "# 体检说明\n\n受检者证件号 11010519491231002X 请核查。\n" * 3
    doc_id = _precreate_document_row("pii-hash.md")
    res = document_indexer.index("pii-hash.md", evil.encode("utf-8"),
                                 kb_id="test-kb", user_id="test-user",
                                 document_id=doc_id)
    assert res["status"] == "indexed"

    with get_db_ctx() as session:
        stored_hash = session.query(Document).filter(
            Document.document_id == doc_id
        ).first().content_hash

    # 复刻 indexer 的清洗 + PII 脱敏路径，得到"脱敏后文本"的期望 hash
    text = document_cleaner.clean(document_parser.parse_bytes(evil.encode("utf-8"), "pii-hash.md"))
    if settings.pii_enabled:
        from app.core.pii_scanner import mask_text, scan, scan_and_reject
        assert not scan_and_reject(text)          # 本测试走 mask 而非 reject
        masked = mask_text(text, findings=scan(text))
        text = masked

    # 关键断言：落库 hash == 脱敏后文本 hash（旧实现 = 原文 hash，会在此失败）
    assert stored_hash == _content_hash(text)


def test_index_without_precreated_document_row(integration_db, fake_llm_stack):
    """期望：indexer 自身能在 chunks 之前建好 Document 行。当前返回 failed。"""
    from app.ingestion.indexer import document_indexer

    res = document_indexer.index(
        "standalone.md",
        "# 独立摄入\n\n这段内容用于验证无预建 Document 行的摄入路径。\n".encode(),
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
