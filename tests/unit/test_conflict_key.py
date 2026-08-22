"""ConflictKey dataclass 单测 — Phase 4-A 数据契约验证。

不测试 detector/extractor（那些是 Phase 4-B）。本测试仅验证：
1. 5 元组字段存取正确
2. frozen 不可变
3. matches_except_value 语义正确
4. hashable + 可作 dict key
5. Sentinel 常量值正确
"""

from app.core.conflict_key import (
    DEFAULT_SCOPE,
    UNKNOWN,
    ConflictKey,
)

# ── 字段存取 ───────────────────────────────────────────────────────


def test_conflict_key_has_5_fields():
    key = ConflictKey(
        entity="挖掘机械",
        metric="销售收入",
        period="2023年",
        unit="亿元",
        scope="公司整体",
    )
    assert key.entity == "挖掘机械"
    assert key.metric == "销售收入"
    assert key.period == "2023年"
    assert key.unit == "亿元"
    assert key.scope == "公司整体"


def test_conflict_key_default_field_independence():
    """每个字段独立 — 改 entity 不影响其他字段。"""
    k1 = ConflictKey("A", "M", "P", "U", "S")
    k2 = ConflictKey("X", "M", "P", "U", "S")
    assert k1.entity == "A"
    assert k2.entity == "X"
    assert k1.metric == k2.metric  # 其他字段不变


# ── frozen 不可变 ───────────────────────────────────────────────────


def test_conflict_key_is_frozen():
    key = ConflictKey("A", "M", "P", "U", "S")
    try:
        key.entity = "X"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except Exception as e:
        assert "FrozenInstanceError" in type(e).__name__ or "frozen" in str(e).lower()


def test_conflict_key_supports_equality():
    k1 = ConflictKey("A", "M", "P", "U", "S")
    k2 = ConflictKey("A", "M", "P", "U", "S")
    assert k1 == k2


def test_conflict_key_inequality_on_any_field():
    base = ConflictKey("挖掘机械", "销售收入", "2023年", "亿元", "公司整体")
    # 任何字段不同 → 不等
    assert base != ConflictKey("起重机械", "销售收入", "2023年", "亿元", "公司整体")
    assert base != ConflictKey("挖掘机械", "营业收入", "2023年", "亿元", "公司整体")
    assert base != ConflictKey("挖掘机械", "销售收入", "2024年", "亿元", "公司整体")
    assert base != ConflictKey("挖掘机械", "销售收入", "2023年", "万元", "公司整体")
    assert base != ConflictKey("挖掘机械", "销售收入", "2023年", "亿元", "母公司")


# ── matches_except_value ──────────────────────────────────────────


def test_matches_except_value_true_when_all_match():
    k1 = ConflictKey("挖掘机械", "销售收入", "2023年", "亿元", "公司整体")
    k2 = ConflictKey("挖掘机械", "销售收入", "2023年", "亿元", "公司整体")
    assert k1.matches_except_value(k2) is True


def test_matches_except_value_false_on_entity_diff():
    k1 = ConflictKey("挖掘机械", "销售收入", "2023年", "亿元", "公司整体")
    k2 = ConflictKey("起重机械", "销售收入", "2023年", "亿元", "公司整体")
    assert k1.matches_except_value(k2) is False


def test_matches_except_value_false_on_period_diff():
    k1 = ConflictKey("公司整体", "营业收入", "2023年", "亿元", "公司整体")
    k2 = ConflictKey("公司整体", "营业收入", "2024年", "亿元", "公司整体")
    assert k1.matches_except_value(k2) is False


def test_matches_except_value_false_on_scope_diff():
    k1 = ConflictKey("公司整体", "营业收入", "2023年", "亿元", "合并报表")
    k2 = ConflictKey("公司整体", "营业收入", "2023年", "亿元", "母公司")
    assert k1.matches_except_value(k2) is False


def test_matches_except_value_false_on_unit_diff():
    k1 = ConflictKey("公司整体", "营业收入", "2023年", "亿元", "公司整体")
    k2 = ConflictKey("公司整体", "营业收入", "2023年", "万元", "公司整体")
    assert k1.matches_except_value(k2) is False


def test_matches_except_value_false_on_metric_diff():
    k1 = ConflictKey("公司整体", "营业收入", "2023年", "亿元", "公司整体")
    k2 = ConflictKey("公司整体", "净利润", "2023年", "亿元", "公司整体")
    assert k1.matches_except_value(k2) is False


# ── hashable / dict key ────────────────────────────────────────────


def test_conflict_key_is_hashable():
    k = ConflictKey("A", "M", "P", "U", "S")
    assert hash(k) is not None


def test_conflict_key_equal_objects_have_same_hash():
    k1 = ConflictKey("A", "M", "P", "U", "S")
    k2 = ConflictKey("A", "M", "P", "U", "S")
    assert hash(k1) == hash(k2)


def test_conflict_key_usable_as_dict_key():
    """核心需求：detector 用 key 分组。"""
    k1 = ConflictKey("A", "M", "P", "U", "S")
    k2 = ConflictKey("A", "M", "P", "U", "S")
    k3 = ConflictKey("B", "M", "P", "U", "S")
    bucket = {}
    bucket[k1] = "value1"
    bucket[k3] = "value3"
    # k2 与 k1 相等 → 应覆盖 k1
    assert bucket[k2] == "value1"
    assert bucket[k3] == "value3"
    assert len(bucket) == 2


def test_conflict_key_usable_in_set():
    k1 = ConflictKey("A", "M", "P", "U", "S")
    k2 = ConflictKey("A", "M", "P", "U", "S")  # duplicate of k1
    k3 = ConflictKey("B", "M", "P", "U", "S")
    s = {k1, k2, k3}
    assert len(s) == 2  # k1 and k2 dedup


# ── Sentinels ──────────────────────────────────────────────────────


def test_unknown_sentinel_value():
    assert UNKNOWN == "未知"


def test_default_scope_sentinel_value():
    assert DEFAULT_SCOPE == "公司整体"


def test_sentinels_are_distinct_strings():
    assert UNKNOWN != DEFAULT_SCOPE


def test_conflict_key_with_unknown_marker_is_usable():
    """失败兜底 key 必须可构造、可比较。"""
    k = ConflictKey(UNKNOWN, UNKNOWN, "", UNKNOWN, DEFAULT_SCOPE)
    assert k.entity == UNKNOWN
    assert k.metric == UNKNOWN
    assert k.period == ""
    assert k.unit == UNKNOWN
    assert k.scope == DEFAULT_SCOPE
    # 仍可哈希、仍可比
    k_copy = ConflictKey(UNKNOWN, UNKNOWN, "", UNKNOWN, DEFAULT_SCOPE)
    assert k == k_copy
    assert hash(k) == hash(k_copy)


# ── 反向 case（spec §5.2）─────────────────────────────────────────


def test_case_c1_different_entity_same_others_no_conflict():
    """C1: 不同 entity → not conflict（即使 metric/period/unit/scope 全相同）。"""
    k1 = ConflictKey("挖掘机械", "销售收入", "2023年", "亿元", "公司整体")
    k2 = ConflictKey("起重机械", "销售收入", "2023年", "亿元", "公司整体")
    assert k1.matches_except_value(k2) is False


def test_case_c2_different_year_same_others_no_conflict():
    """C2: 不同 year → not conflict (year_mismatch 方向)。"""
    k1 = ConflictKey("公司整体", "销售收入", "2023年", "亿元", "公司整体")
    k2 = ConflictKey("公司整体", "销售收入", "2024年", "亿元", "公司整体")
    assert k1.matches_except_value(k2) is False


def test_case_c4_same_5tuple_is_potential_conflict():
    """C4: 5 元组完全相同 → may conflict (true conflict if values differ)。"""
    k1 = ConflictKey("公司整体", "营业收入", "2023年", "亿元", "合并报表")
    k2 = ConflictKey("公司整体", "营业收入", "2023年", "亿元", "合并报表")
    assert k1.matches_except_value(k2) is True
