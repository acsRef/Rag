"""搜索层错误可见性：原始异常上抛、微秒边界写入。"""


def test_search_propagates_original_error(monkeypatch):
    """DB 出错时上抛原始异常，而不是 finally 里的 UnboundLocalError。"""
    from app.store import pgvector_store

    class BrokenSession:
        def execute(self, *a, **kw):
            raise RuntimeError("db down")

        def close(self):
            pass

    monkeypatch.setattr(pgvector_store, "get_session", lambda: BrokenSession())
    try:
        pgvector_store.search(["test-kb"], [0.1] * 4096, can_read_all=True)
        raise AssertionError("应当抛异常")
    except RuntimeError as e:
        assert "db down" in str(e)


def test_add_chunks_microsecond_overflow(integration_db):
    """基准微秒接近上限时，多 chunk 写入不得 ValueError。"""
    from app.store import pgvector_store
    from app.store.db import Document, get_db_ctx, utc_now

    # chunks 外键引用 documents，先按 API 契约建行
    with get_db_ctx() as session:
        if not session.query(Document).filter_by(document_id="ovf-doc").first():
            session.add(Document(document_id="ovf-doc", kb_id="test-kb",
                                 filename="ovf.md", owner_id="test-user",
                                 status="indexing"))
            session.commit()

    base = utc_now().replace(microsecond=999998)
    original = pgvector_store.utc_now
    pgvector_store.utc_now = lambda: base
    try:
        pgvector_store.add_chunks([
            {"chunk_id": "ovf_%d" % i, "document_id": "ovf-doc", "kb_id": "test-kb",
             "text": "溢出测试 %d" % i, "embedding": [0.1] * 4096}
            for i in range(4)
        ])
    finally:
        pgvector_store.utc_now = original
    got = pgvector_store.get_chunks_by_document("ovf-doc")
    assert len(got) == 4
