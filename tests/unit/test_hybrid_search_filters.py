"""app/store/pgvector_store.py::hybrid_search 新增 filters 参数的契约测试。

锁定 Day 1 下午契约：
- filters: RetrievalFilter | None = None，向后兼容（默认 None = 不传过滤）
- filters.document_ids 必须覆盖/合并旧 document_ids 参数（filters 优先）
- filters.years / filters.section_names / filters.source_types / filters.kb_ids
  暂不翻译到 SQL（Day 2 上午 chunks 表加 year 列等再接通；本测试只验证接口稳定）
- 三个通道函数（search / bm25_search / question_vector_search）必须收到相同的
  document_ids 列表
"""

import pytest

from app.core.retrieval_filter import RetrievalFilter
from app.store import pgvector_store


@pytest.fixture
def capture_channels(monkeypatch):
    """把三个通道函数替换为 capture-only stub，返回 []。stub 接 *args + **kwargs
    因为既有调用是 positional（search/...）又有 keyword（document_ids=...）。

    注：hybrid_search 在主流程后还会触发一次 fallback bm25_search 重写 captured，
    用 list 累积所有调用让测试断言"看到过 document_ids"，而不是"最后一次调用有"。
    """
    captured: dict[str, list[dict]] = {}

    def make_stub(name):
        def stub(*args, **kwargs):
            captured.setdefault(name, []).append({"args": args, "kwargs": kwargs})
            return []

        return stub

    monkeypatch.setattr(pgvector_store, "search", make_stub("vector"))
    monkeypatch.setattr(pgvector_store, "bm25_search", make_stub("bm25"))
    monkeypatch.setattr(pgvector_store, "question_vector_search", make_stub("question"))
    return captured


def _doc_ids_of(channel_capture):
    """从任一次 stub 捕获里提取 document_ids（无论 positional 还是 kwargs）。"""
    for entry in channel_capture:
        if "document_ids" in entry["kwargs"]:
            return entry["kwargs"]["document_ids"]
    return None


def _saw_doc_ids(channel_capture, expected):
    """stub 多次调用中至少有一次把 document_ids 设为 expected。"""
    expected_set = set(expected) if expected else expected
    for entry in channel_capture:
        got = entry["kwargs"].get("document_ids")
        if got is None and expected is None:
            return True
        if got is not None and expected_set is not None and set(got) == expected_set:
            return True
    return False


def test_hybrid_search_accepts_filters_none_without_breaking(capture_channels):
    """filters=None 时行为与改造前完全一致。"""
    pgvector_store.hybrid_search(
        kb_ids=["kb1"],
        embedding=[0.0] * 4,
        query="q",
    )
    # search 与 bm25_search 必须被调用；document_ids 缺省 = 不传
    assert "vector" in capture_channels
    assert "bm25" in capture_channels
    # 既没传 filters 也没传 document_ids → 主流程 document_ids=None
    assert any(e["kwargs"].get("document_ids") is None for e in capture_channels["vector"])


def test_hybrid_search_filters_document_ids_propagates_to_all_channels(capture_channels):
    """filters.document_ids 必须传到 vector + bm25 + question 三个通道。"""
    pgvector_store.hybrid_search(
        kb_ids=["kb1"],
        embedding=[0.0] * 4,
        query="q",
        filters=RetrievalFilter(document_ids={"doc_a", "doc_b"}),
        enable_question_channel=True,
    )
    expected = {"doc_a", "doc_b"}
    for ch in ("vector", "bm25", "question"):
        assert ch in capture_channels, f"{ch} 通道未被调用"
        assert _saw_doc_ids(capture_channels[ch], expected), (
            f"{ch} 通道未传 document_ids={expected}"
        )


def test_hybrid_search_filters_overrides_legacy_document_ids(capture_channels):
    """filters.document_ids 与旧 document_ids 同时传时，filters 优先。"""
    pgvector_store.hybrid_search(
        kb_ids=["kb1"],
        embedding=[0.0] * 4,
        query="q",
        document_ids=["legacy_doc"],  # 旧参数
        filters=RetrievalFilter(document_ids={"new_doc"}),  # 新参数
    )
    assert _saw_doc_ids(capture_channels["vector"], {"new_doc"})
    assert _saw_doc_ids(capture_channels["bm25"], {"new_doc"})


def test_hybrid_search_legacy_document_ids_still_works(capture_channels):
    """只传老 document_ids 时仍正常工作（向后兼容）。"""
    pgvector_store.hybrid_search(
        kb_ids=["kb1"],
        embedding=[0.0] * 4,
        query="q",
        document_ids=["legacy_only"],
    )
    assert _saw_doc_ids(capture_channels["vector"], {"legacy_only"})
    assert _saw_doc_ids(capture_channels["bm25"], {"legacy_only"})


def test_hybrid_search_filters_empty_does_not_pass_document_ids(capture_channels):
    """filters=RetrievalFilter() 空过滤器 → 跟 filters=None 一样：document_ids=None。"""
    pgvector_store.hybrid_search(
        kb_ids=["kb1"],
        embedding=[0.0] * 4,
        query="q",
        filters=RetrievalFilter(),
    )
    # 空过滤器不应产生 document_ids 过滤（即使 None）
    for ch in ("vector", "bm25"):
        assert all(e["kwargs"].get("document_ids") is None for e in capture_channels[ch])


def test_hybrid_search_filters_unused_fields_documented_as_noop(capture_channels):
    """years/section_names/source_types/kb_ids 暂未翻译；本测试只锁定"接口不报错"。"""
    # 即便这些字段填了值，当前不会翻译到 SQL（Day 2 接通）
    pgvector_store.hybrid_search(
        kb_ids=["kb1"],
        embedding=[0.0] * 4,
        query="q",
        filters=RetrievalFilter(
            years={2024, 2025},
            section_names={"主要会计数据"},
            source_types={"annual"},
            kb_ids={"kb_x"},
        ),
    )
    # 调用不报错，channels 都被触达；当前实现不传 years 等
    assert "vector" in capture_channels
    assert "bm25" in capture_channels
