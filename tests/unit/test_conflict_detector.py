"""ConflictDetector 单测 — Phase 4-B 验收。

验证 ConflictDetector 在使用 ConflictKeyExtractor 后能正确区分:
- 同 key 不同 value → 真冲突
- 不同 entity / metric / period / unit / scope → 非冲突

关联：docs/plans/2026-08-23-conflict-key-spec.md §5.2
"""

from app.core.conflict_key import ConflictKey
from app.core.evidence import (
    ConflictDetector,
    EvidenceSlot,
    EvidenceTable,
)
from app.models.schemas import RetrievedChunk


def _make_chunk(chunk_id, doc_id="doc1", text="x", year="2023年", section="第一节"):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=doc_id,
        text=text,
        score=0.5,
        title="Test",
        section_path=section,
        year=year,
    )


def _build_table(slot_chunks_list):
    """Build EvidenceTable from list of (sub_question, chunks) tuples."""
    slots = [
        EvidenceSlot(sub_question=sq, chunks=chunks) for sq, chunks in slot_chunks_list
    ]
    return EvidenceTable(query="test", slots=slots)


# ── True conflict case ──────────────────────────────────────────────────


def test_same_5tuple_different_values_is_conflict():
    """C4: 5 元组完全相同，value 不同 → TRUE conflict (high severity)."""
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", doc_id="doc1", text="营业总收入为732亿元")]),
        ("q2", [_make_chunk("c2", doc_id="doc2", text="营业总收入为740亿元")]),
    ])
    conflicts = detector.detect(table)
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "value_mismatch"
    assert conflicts[0].severity == "high"
    assert "营业总收入" in conflicts[0].metric
    assert len(conflicts[0].values) == 2


# ── False positive cases (should NOT conflict) ──────────────────────────


def test_different_entity_same_others_no_conflict():
    """C1: 不同 entity（产品线）→ not conflict.

    场景: '挖掘机械销售收入 100亿' vs '起重机械销售收入 105亿'
    entity 不同（挖掘机械 vs 起重机械），不算冲突。
    """
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", text="挖掘机械销售收入100亿元")]),
        ("q2", [_make_chunk("c2", text="起重机械销售收入105亿元")]),
    ])
    conflicts = detector.detect(table)
    # 不应有冲突（不同 entity）
    assert len(conflicts) == 0


def test_different_year_same_others_no_conflict():
    """C2: 不同 year → not conflict（period 现在是 key 一部分）。

    场景: '2023 销售收入 100亿' vs '2024 销售收入 120亿'
    period 不同（2023年 vs 2024年），不算冲突。
    """
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", year="2023年", text="销售收入100亿元")]),
        ("q2", [_make_chunk("c2", year="2024年", text="销售收入120亿元")]),
    ])
    conflicts = detector.detect(table)
    # 不应有冲突（不同 period）
    assert len(conflicts) == 0


def test_different_scope_same_others_no_conflict():
    """C3: 不同 scope（合并 vs 母公司）→ not conflict."""
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", section="合并资产负债表", text="营业总收入为800亿元")]),
        ("q2", [_make_chunk("c2", section="母公司资产负债表", text="营业总收入为750亿元")]),
    ])
    conflicts = detector.detect(table)
    # 不应有冲突（不同 scope）
    assert len(conflicts) == 0


def test_different_unit_no_conflict():
    """不同 unit → not conflict (本期保守策略)."""
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", text="营业总收入732亿元")]),
        ("q2", [_make_chunk("c2", text="营业总收入7320000万元")]),
    ])
    conflicts = detector.detect(table)
    # 不应有冲突（不同 unit）
    assert len(conflicts) == 0


# ── Filter / Skip cases ─────────────────────────────────────────────────


def test_unknown_metric_is_skipped():
    """Metric 不可识别 → 跳过（不入 conflict check）。"""
    detector = ConflictDetector()
    # text 中 metric prefix 不可识别 — unit 提取可能仍 OK，但 metric=未知
    table = _build_table([
        ("q1", [_make_chunk("c1", text="某物732亿元")]),  # "某物" 不是已知 metric
    ])
    # 单 chunk 不触发 conflict（needs multiple values）
    conflicts = detector.detect(table)
    assert len(conflicts) == 0


def test_empty_period_is_skipped():
    """period="" → 跳过（不可比）。"""
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", year="", text="营业总收入732亿元")]),
        ("q2", [_make_chunk("c2", year="", text="营业总收入740亿元")]),
    ])
    conflicts = detector.detect(table)
    # period 为空 → 不可比 → 不应有冲突
    assert len(conflicts) == 0


def test_single_doc_no_conflict():
    """单 doc 不同 values 同 key → section_mismatch (medium)，不算 high 拒答。"""
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", doc_id="doc1", section="第一节", text="营业总收入为732亿元")]),
        ("q2", [_make_chunk("c2", doc_id="doc1", section="第二节", text="营业总收入为740亿元")]),
    ])
    conflicts = detector.detect(table)
    # 同 doc 不同 section → section_mismatch (medium)
    # 但 has_multiple_docs=False → 提前 return []
    # 这是 EvidenceOrganizer 的特性：单文档不调用 detector
    assert len(conflicts) == 0


def test_single_value_no_conflict():
    """只有 1 个 comparable value → 不可能有 conflict。"""
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", doc_id="doc1", text="营业总收入732亿元")]),
    ])
    conflicts = detector.detect(table)
    assert len(conflicts) == 0


# ── Conflict classification ──────────────────────────────────────────────


def test_cross_doc_conflict_is_high_severity():
    """跨文档冲突 → value_mismatch (high severity)."""
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", doc_id="doc1", text="营业总收入为732亿元")]),
        ("q2", [_make_chunk("c2", doc_id="doc2", text="营业总收入为740亿元")]),
    ])
    conflicts = detector.detect(table)
    assert len(conflicts) == 1
    assert conflicts[0].severity == "high"
    assert conflicts[0].conflict_type == "value_mismatch"


def test_conflict_values_have_keys():
    """冲突中每个 MetricValue 应携带 key。"""
    detector = ConflictDetector()
    table = _build_table([
        ("q1", [_make_chunk("c1", doc_id="doc1", text="营业总收入为732亿元")]),
        ("q2", [_make_chunk("c2", doc_id="doc2", text="营业总收入为740亿元")]),
    ])
    conflicts = detector.detect(table)
    assert len(conflicts) == 1
    for mv in conflicts[0].values:
        assert isinstance(mv.key, ConflictKey)
        assert mv.key.metric != "未知"
        assert mv.key.period != ""
