"""app/core/retrieval_filter.py 测试：RetrievalFilter dataclass（Day 1 下午）。

锁定 dataclass 契约：
- frozen：检索 filter 是查询意图快照，构造后不可改
- 全 Optional + None：未设 = 不限（不构造空集合干扰判断）
- frozenset 而非 set：frozen=True 要求字段可哈希；frozenset 也更明确"只读"
- set 输入自动转 frozenset（外部传 list/set 也行）
"""
import pytest

from app.core.retrieval_filter import RetrievalFilter


def test_default_construction_all_none():
    """不传任何字段 = 不限；显式 None 与"不传"等价。"""
    f = RetrievalFilter()
    assert f.years is None
    assert f.document_ids is None
    assert f.section_names is None
    assert f.source_types is None
    assert f.kb_ids is None


def test_frozen_prevents_mutation():
    """frozen=True：构造后字段不能赋值修改。"""
    f = RetrievalFilter(years={2024})
    with pytest.raises((AttributeError, TypeError)):
        f.years = {2025}  # type: ignore[misc]


def test_hashable_for_dict_key_use():
    """hashable：可作为缓存 key 或 set 元素。"""
    f1 = RetrievalFilter(years={2024}, document_ids={"doc1"})
    f2 = RetrievalFilter(years={2024}, document_ids={"doc1"})
    assert hash(f1) == hash(f2)
    assert {f1, f2} == {f1}  # 相等对象去重


def test_set_inputs_become_frozenset():
    """list/set 输入也接受——但内部存为 frozenset 保证可哈希。"""
    f = RetrievalFilter(
        years=[2024, 2025],
        document_ids={"doc1", "doc2"},
        section_names={"主要会计数据"},
    )
    assert isinstance(f.years, frozenset)
    assert f.years == frozenset({2024, 2025})
    assert f.document_ids == frozenset({"doc1", "doc2"})
    assert f.section_names == frozenset({"主要会计数据"})


def test_frozenset_inputs_preserved():
    f = RetrievalFilter(years=frozenset({2024}))
    assert isinstance(f.years, frozenset)
    assert f.years == frozenset({2024})


def test_equality_is_value_based():
    """同字段值的两个 RetrievalFilter 必须相等（frozen dataclass 默认行为）。"""
    f1 = RetrievalFilter(years={2024}, document_ids={"doc1"})
    f2 = RetrievalFilter(years={2024}, document_ids={"doc1"})
    assert f1 == f2


def test_inequality_when_field_differs():
    f1 = RetrievalFilter(years={2024})
    f2 = RetrievalFilter(years={2025})
    assert f1 != f2


def test_is_empty_when_all_none():
    """辅助语义：所有字段为 None 时视为"未设过滤器"——用于 hybrid_search 短路。"""
    assert RetrievalFilter().is_empty() is True


def test_is_not_empty_when_any_field_set():
    """任一字段非 None = 过滤器生效。"""
    assert RetrievalFilter(years={2024}).is_empty() is False
    assert RetrievalFilter(document_ids={"x"}).is_empty() is False
    assert RetrievalFilter(section_names={"s"}).is_empty() is False
    assert RetrievalFilter(source_types={"annual"}).is_empty() is False
    assert RetrievalFilter(kb_ids={"kb1"}).is_empty() is False
