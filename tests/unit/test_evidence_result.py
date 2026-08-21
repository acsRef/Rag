"""app/core/evidence.py::EvidenceResult + build_evidence_result + gate helpers（Day 2 下午）。

plan §五 + §十风险 §7：
- 不重新设计 EvidenceTable（已有 314 行活代码）
- 只暴露 EvidenceResult 包装：coverage / temporal_consistent / conflicts / sources / coverage_by_year
- build_evidence_result(table) 转换
- evidence_gate_should_refuse(result, threshold) → bool 给 pipeline 用
"""
import pytest

from app.core.evidence import (
    EvidenceResult,
    build_evidence_result,
    evidence_gate_should_refuse,
)
from app.core.evidence import EvidenceTable, EvidenceSlot


def _slot(sub_q, doc_ids, covered=True):
    """构造一个 EvidenceSlot；空 chunks 表示 uncovered."""
    from app.models.schemas import RetrievedChunk
    chunks = []
    if covered:
        for doc_id in doc_ids:
            chunks.append(RetrievedChunk(
                chunk_id=f"c_{doc_id}", document_id=doc_id, text=f"text for {doc_id}",
                score=0.5, title="", section_path="",
            ))
    return EvidenceSlot(sub_question=sub_q, chunks=chunks)


# ── EvidenceResult 构造 ────────────────────────────────


def test_evidence_result_required_fields():
    """EvidenceResult 必须有 plan §五.1 要求的 5 个字段。"""
    r = EvidenceResult(
        coverage=0.8,
        temporal_consistent=True,
        conflicts=[],
        sources=[],
        coverage_by_year={2024: 1.0},
    )
    assert r.coverage == 0.8
    assert r.temporal_consistent is True
    assert r.conflicts == []
    assert r.sources == []
    assert r.coverage_by_year == {2024: 1.0}


def test_evidence_result_coverage_bounded():
    """coverage 默认 0~1；EvidenceResult 不强制 bound（仅 build_evidence_result 计算时 clip）。"""
    r = EvidenceResult(coverage=1.5, temporal_consistent=True, conflicts=[], sources=[])
    assert r.coverage == 1.5  # 直接构造允许越界，build 时再 clip


# ── build_evidence_result 转换 ────────────────────────


def test_build_evidence_result_full_coverage():
    """所有 slot 都有 chunks → coverage=1.0"""
    table = EvidenceTable(
        query="q",
        slots=[
            _slot("sub1", {"d1"}),
            _slot("sub2", {"d2"}),
        ],
    )
    r = build_evidence_result(table)
    assert r.coverage == 1.0
    assert r.temporal_consistent is True
    assert r.conflicts == []
    # sources 应该包含 2 个 chunk（来自 d1 + d2），在 sources_collected 测试里详细断言
    assert len(r.sources) == 2
    assert r.coverage_by_year == {}


def test_build_evidence_result_partial_coverage():
    """部分 slot 无 chunks → coverage=covered/total"""
    table = EvidenceTable(
        query="q",
        slots=[
            _slot("sub1", {"d1"}),         # covered
            _slot("sub2", set(), covered=False),  # uncovered
            _slot("sub3", {"d3"}),         # covered
        ],
    )
    r = build_evidence_result(table)
    assert r.coverage == pytest.approx(2 / 3)


def test_build_evidence_result_empty_table():
    """无 slot → coverage=0，不抛错"""
    table = EvidenceTable(query="q", slots=[])
    r = build_evidence_result(table)
    assert r.coverage == 0.0
    assert r.temporal_consistent is True  # 空表无冲突，视为 consistent
    assert r.conflicts == []
    assert r.sources == []
    assert r.coverage_by_year == {}


def test_build_evidence_result_sources_collected():
    """sources 字段收集所有 chunk 的来源（chunk_id → document_id 映射）"""
    table = EvidenceTable(
        query="q",
        slots=[_slot("sub1", {"d1", "d2"}), _slot("sub2", {"d3"})],
    )
    r = build_evidence_result(table)
    # 每个 chunk 都成 source
    assert len(r.sources) == 3
    src_doc_ids = {s["document_id"] for s in r.sources}
    assert src_doc_ids == {"d1", "d2", "d3"}


def test_build_evidence_result_passes_through_conflicts():
    """EvidenceTable.conflicts 透传到 EvidenceResult.conflicts"""
    from app.core.evidence import Conflict
    c = Conflict(metric="revenue", values=[], severity="high")
    table = EvidenceTable(query="q", slots=[_slot("s", {"d1"})], conflicts=[c])
    r = build_evidence_result(table)
    assert r.conflicts == [c]


def test_build_evidence_result_temporal_consistent_false_on_conflicts():
    """有冲突 → temporal_consistent=False（plan §五.1）"""
    from app.core.evidence import Conflict
    c = Conflict(metric="x", values=[], conflict_type="year_mismatch")
    table = EvidenceTable(query="q", slots=[_slot("s", {"d1", "d2"})], conflicts=[c])
    r = build_evidence_result(table)
    assert r.temporal_consistent is False


def test_build_evidence_result_coverage_clipped():
    """空 slots → coverage 应被 clip 到 0（避免除零）"""
    table = EvidenceTable(query="q", slots=[])
    r = build_evidence_result(table)
    assert 0.0 <= r.coverage <= 1.0


# ── coverage_by_year ─────────────────────────────────


def test_build_evidence_result_coverage_by_year_empty_when_no_year_field():
    """RetrievedChunk 无 year 字段时 coverage_by_year = {}（避免 LLM 误读噪声）"""
    table = EvidenceTable(query="q", slots=[_slot("s", {"d1"})])
    r = build_evidence_result(table)
    assert r.coverage_by_year == {}


def test_build_evidence_result_coverage_by_year_uses_chunk_year():
    """当 chunks 有 year 字段时，按年份统计覆盖"""
    from app.models.schemas import RetrievedChunk
    chunks_2024 = [RetrievedChunk(
        chunk_id="c1", document_id="d1", text="2024 data", score=0.5,
        title="", section_path="",
    )]
    chunks_2024[0].year = "2024年"
    chunks_2023 = [RetrievedChunk(
        chunk_id="c2", document_id="d2", text="2023 data", score=0.5,
        title="", section_path="",
    )]
    chunks_2023[0].year = "2023年"
    table = EvidenceTable(query="q", slots=[
        EvidenceSlot(sub_question="s1", chunks=chunks_2024),
        EvidenceSlot(sub_question="s2", chunks=chunks_2023),
    ])
    r = build_evidence_result(table)
    # 2 个 slot，每个 slot 都来自单一 year；每个 year 覆盖 1/2 slot
    assert r.coverage_by_year == {"2024年": 0.5, "2023年": 0.5}


# ── evidence_gate_should_refuse ─────────────────────


def test_refuse_when_coverage_below_threshold():
    r = EvidenceResult(coverage=0.5, temporal_consistent=True, conflicts=[], sources=[])
    assert evidence_gate_should_refuse(r, threshold=0.7) is True


def test_pass_when_coverage_meets_threshold():
    r = EvidenceResult(coverage=0.7, temporal_consistent=True, conflicts=[], sources=[])
    assert evidence_gate_should_refuse(r, threshold=0.7) is False


def test_pass_when_coverage_above_threshold():
    r = EvidenceResult(coverage=0.9, temporal_consistent=True, conflicts=[], sources=[])
    assert evidence_gate_should_refuse(r, threshold=0.7) is False


def test_refuse_on_temporal_inconsistency_even_if_coverage_high():
    """高 coverage 但 temporal_consistent=False → 仍拒答（plan §五.2）"""
    r = EvidenceResult(coverage=0.95, temporal_consistent=False, conflicts=[], sources=[])
    assert evidence_gate_should_refuse(r, threshold=0.7) is True


def test_no_refuse_on_zero_coverage_if_threshold_is_zero():
    """threshold=0 时即使 coverage=0 也不拒答（边界）"""
    r = EvidenceResult(coverage=0.0, temporal_consistent=True, conflicts=[], sources=[])
    assert evidence_gate_should_refuse(r, threshold=0.0) is False
