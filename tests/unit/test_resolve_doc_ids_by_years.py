"""app/core/pipeline.py::resolve_doc_ids_by_years 测试（Day 1 晚上）。

锁定：
- years=[2024] + kb_id 已知 → 返回该 KB 中匹配 '2024' 的 document_ids
- years=[] 或 None → 返回 []
- 同步 DB 调用（用同步 session 测试，不依赖事件循环）
- 无匹配 → 返回 []
"""
from app.core.pipeline import resolve_doc_ids_by_years


def test_resolve_doc_ids_by_years_empty_input():
    """空 years → 空 list"""
    session = object()  # 不会被调用
    assert resolve_doc_ids_by_years([], ["kb-x"], session) == []
    assert resolve_doc_ids_by_years(None, ["kb-x"], session) == []


def test_resolve_doc_ids_by_years_single_year(monkeypatch):
    """单年 → 查 Document 表返回的 doc_ids"""
    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self
        def all(self):
            return [("doc_2024",)]

    class FakeSession:
        def query(self, model):
            return FakeQuery()

    # monkeypatch Document model to be importable inside the function
    from app.store import db as db_mod
    monkeypatch.setattr(db_mod, "Document", type("Document", (), {}), raising=False)

    result = resolve_doc_ids_by_years([2024], ["kb-x"], FakeSession())
    assert result == ["doc_2024"]


def test_resolve_doc_ids_by_years_multiple_years(monkeypatch):
    """多年 → 合并去重"""
    captured_filters = []

    class FakeQuery:
        def filter(self, *args, **kwargs):
            captured_filters.append(kwargs)
            return self
        def all(self):
            # 第一次 filter (kb_id IN) → 返回空
            # 第二次 filter (doc_id IN) → 返回 docs
            return [("doc_2024",), ("doc_2025",)]

    class FakeSession:
        def __init__(self):
            self.calls = 0
        def query(self, model):
            self.calls += 1
            return FakeQuery()

    from app.store import db as db_mod
    monkeypatch.setattr(db_mod, "Document", type("Document", (), {}), raising=False)

    session = FakeSession()
    result = resolve_doc_ids_by_years([2024, 2025], ["kb-x"], session)
    assert set(result) == {"doc_2024", "doc_2025"}


def test_resolve_doc_ids_by_years_no_match(monkeypatch):
    """无匹配年份 → 空 list"""
    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self
        def all(self):
            return []

    class FakeSession:
        def query(self, model):
            return FakeQuery()

    from app.store import db as db_mod
    monkeypatch.setattr(db_mod, "Document", type("Document", (), {}), raising=False)

    result = resolve_doc_ids_by_years([1990], ["kb-x"], FakeSession())
    assert result == []
