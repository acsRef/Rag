"""设计审查 P1-7：_collect_results 并行检索各 KB（asyncio.gather + to_thread）。

旧实现逐 KB 串行调 hybrid_search（每 KB 最多 4 次顺序 DB 查询）。
这里验证：① 各 KB 检索并发发生；② 跨 KB 去重；③ 归属 kb_id 标注正确。
"""
import threading

import app.core.retrieval as retrieval_mod


async def test_collect_results_runs_kbs_concurrently(monkeypatch):
    """3 个 KB 用 Barrier 同步：若非并行，首个调用会等不到其余两个而超时。"""
    N = 3
    barrier = threading.Barrier(N)

    def fake_search(kb_id, query_emb, query, user_role_ids, can_read_all, top_k, user_id="", document_ids=None, filters=None):
        barrier.wait(timeout=5)   # 三个检索必须同时到达
        return [{"chunk_id": f"{kb_id}-0", "score": 0.5, "text": "x"}]

    monkeypatch.setattr(retrieval_mod, "_search_kb", fake_search)

    seen: set = set()
    results: list = []
    await retrieval_mod._collect_results(
        ["kb1", "kb2", "kb3"], [], "q", None, True, 5, seen, results,
    )
    assert {r["chunk_id"] for r in results} == {"kb1-0", "kb2-0", "kb3-0"}


async def test_collect_results_dedups_across_kbs(monkeypatch):
    """同一 chunk_id 出现在多个 KB 结果里时只保留一次，且归属首个出现的 KB。"""
    def fake_search(kb_id, query_emb, query, user_role_ids, can_read_all, top_k, user_id="", document_ids=None, filters=None):
        return [{"chunk_id": "shared-0", "score": 0.5, "text": "x"}]

    monkeypatch.setattr(retrieval_mod, "_search_kb", fake_search)

    seen: set = set()
    results: list = []
    await retrieval_mod._collect_results(
        ["kb1", "kb2"], [], "q", None, True, 5, seen, results,
    )
    assert len(results) == 1
    assert results[0]["chunk_id"] == "shared-0"
    assert results[0]["kb_id"] == "kb1"   # 首个贡献者
    assert seen == {"shared-0"}