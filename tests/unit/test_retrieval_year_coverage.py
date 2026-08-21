"""锁定 retrieval 的 C类跨年覆盖补充：_query_years / _is_cross_year_query / _supplement_missing_years。

C类（跨文档对比）根因是跨年混排导致某年数据缺失。本测试锁定:
- 年份提取与跨年意图判定
- 跨年 query 时补齐缺失年份的 chunk；精确单点 query 时不触发
- 全部依赖(DB/bm25)mock，纯离线。
"""

import app.core.retrieval as retrieval
from app.core.retrieval import _is_cross_year_query, _query_years

# ── 年份提取 ──────────────────────────────────────────────


def test_query_years_extracts_distinct_ordered():
    assert _query_years("2023-2025年营收对比") == ["2023年", "2025年"]
    assert _query_years("2023年、2024年、2024年营收") == ["2023年", "2024年"]
    assert _query_years("近三年营收") == []
    assert _query_years("没有年份的问题") == []


def test_is_cross_year_query():
    assert _is_cross_year_query("近三年海外收入") is True
    assert _is_cross_year_query("2023-2025年营收对比") is True
    assert _is_cross_year_query("还分别是什么") is True
    assert _is_cross_year_query("2023年营收是多少") is False  # 单点
    assert _is_cross_year_query("什么是RAG") is False


# ── 年份覆盖补充 ──────────────────────────────────────────


def _make_result(chunk_id, doc_id, year, score=0.5, text="内容"):
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "year": year,
        "score": score,
        "title": "",
        "text": text,
        "section_path": "",
    }


def test_supplement_skips_non_cross_year(monkeypatch):
    results = [_make_result("a", "d2023", "2023年")]
    out = retrieval._supplement_missing_years(results, "2023年营收", ["kb1"], None, True, "")
    # 单点查询（有年份无跨年）→ 不触发，原样返回
    assert out == results


def test_supplement_appends_missing_related_years(monkeypatch):
    """query 含 2023-2025，但结果只有 2024 和 2025 → 应补 2023。"""
    results = [
        _make_result("r24", "d2024", "2024年", score=0.8),
        _make_result("r25", "d2025", "2025年", score=0.7),
    ]

    fake_rows = [
        ("d2023", "三一重工_2023年年度报告.pdf"),
        ("d2024", "三一重工_2024年年度报告.pdf"),
        ("d2025", "三一重工_2025年年度报告.pdf"),
    ]

    # mock bm25_search: 只对 d2023 的数据返回 chunk
    def fake_bm25(kb_ids, query, **kw):
        doc_ids = kw.get("document_ids") or []
        if "d2023" in doc_ids:
            return [
                {
                    "chunk_id": "r23",
                    "document_id": "d2023",
                    "score": 0.6,
                    "title": "",
                    "text": "2023年数据",
                    "section_path": "2023年",
                }
            ]
        return []

    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *a, **k):
            return self

        def all(self):
            return self._rows

    class FakeSession:
        def __init__(self):
            self._rows = fake_rows

        def query(self, *a, **k):
            return FakeQuery([(rid, fn) for rid, fn in self._rows])

    class FakeCtx:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *a):
            return False

    import app.store.db as db

    monkeypatch.setattr(db, "get_db_ctx", FakeCtx)
    monkeypatch.setattr(retrieval.pgvector_store, "bm25_search", fake_bm25)

    out = retrieval._supplement_missing_years(
        results, "2023-2025年营收对比", ["kb1"], None, True, ""
    )
    years = {r["year"] for r in out}
    assert "2023年" in years, "应补齐缺失的 2023 年"
    assert any(r["chunk_id"] == "r23" for r in out)
