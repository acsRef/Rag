"""锁定文档进度 SSE：按用户过滤 + 满队列丢旧 + 5% 节流。

旧实现：进度事件全员广播（任何登录用户可见他人文档 id/状态/报错），
队列满时 put 失败被静默吞掉（慢客户端可能恰好丢终态事件，UI 卡死在
indexing），且每个 chunk 广播一次（500 块 = 500 条 × 全体订阅者）。
"""
import asyncio

from app.api.documents import _put_or_drop_oldest, _should_deliver
from app.ingestion.indexer import _emit_progress, _progress_buckets

# ── _should_deliver：按用户过滤 ─────────────────────────

def test_event_without_uid_broadcasts():
    assert _should_deliver("", "alice") is True


def test_matching_uid_delivered():
    assert _should_deliver("alice", "alice") is True


def test_other_uid_filtered():
    assert _should_deliver("alice", "bob") is False


def test_admin_channel_receives_all():
    assert _should_deliver("alice", "") is True


# ── _put_or_drop_oldest：满队列丢旧保新 ─────────────────

async def test_full_queue_drops_oldest_keeps_latest():
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    q.put_nowait({"n": 1})
    q.put_nowait({"n": 2})
    _put_or_drop_oldest(q, {"n": 3})
    items = [q.get_nowait(), q.get_nowait()]
    assert [i["n"] for i in items] == [2, 3], "满队列应丢最旧——终态事件必须送达"


async def test_non_full_queue_plain_put():
    q: asyncio.Queue = asyncio.Queue(maxsize=4)
    _put_or_drop_oldest(q, {"n": 1})
    assert q.qsize() == 1


# ── _emit_progress：5% 桶节流 + 终态必发 ────────────────

def test_intermediate_progress_throttled_by_5pct_bucket(monkeypatch):
    import app.api.documents as docs_mod

    sent = []
    monkeypatch.setattr(docs_mod, "emit_doc_progress", lambda e: sent.append(e))
    _progress_buckets.clear()
    try:
        for embedded in (1, 2, 3, 4, 5, 6):   # total=100 → 桶 0,0,0,0,1,1
            _emit_progress("doc-x", "alice", embedded, 100, "indexing")
        assert len(sent) == 2, "同一 5% 桶内的中间进度不应重复广播"
        assert all(e["user_id"] == "alice" for e in sent)
        assert [e["embedded_chunk_count"] for e in sent] == [1, 5]
    finally:
        _progress_buckets.clear()


def test_terminal_status_always_emitted_and_clears_bucket(monkeypatch):
    import app.api.documents as docs_mod

    sent = []
    monkeypatch.setattr(docs_mod, "emit_doc_progress", lambda e: sent.append(e))
    _progress_buckets.clear()
    try:
        _emit_progress("doc-y", "alice", 3, 100, "indexing")
        _emit_progress("doc-y", "alice", 3, 100, "indexing")  # 同桶节流
        _emit_progress("doc-y", "alice", 100, 100, "indexed")  # 终态必发
        assert len(sent) == 2
        assert sent[-1]["status"] == "indexed"
        assert "doc-y" not in _progress_buckets, "终态应清理节流桶"
    finally:
        _progress_buckets.clear()
