"""Evidence Gate severity-aware 决策单测 — Phase 4-C 验收。

Phase 4-C 决策契约:
- high severity (value_mismatch) → temporal_consistent=False → gate refuse
- medium severity (section_mismatch) → temporal_consistent=True → gate pass
- low severity (year_mismatch, 保留向后兼容) → temporal_consistent=True → gate pass

历史 Phase 2-3 阶段: temporal_consistent = (len(conflicts) == 0)
→ 任何冲突都让 gate 拒答（包括 medium severity）
→ 90% refusal 实测

关联：docs/plans/2026-08-23-phase4-evidence-contract-repair.md §4-C
"""

from app.core.conflict_key import ConflictKey
from app.core.evidence import (
    Conflict,
    MetricValue,
    _is_temporally_consistent,
    evidence_gate_should_refuse,
)


# ── Fixtures ────────────────────────────────────────────────────────────


def _make_conflict(severity: str, conflict_type: str = "value_mismatch") -> Conflict:
    """构造一个 Conflict 用于 gate 决策测试。"""
    return Conflict(
        metric="test_metric",
        values=[],
        conflict_type=conflict_type,
        severity=severity,
        resolution_hint="",
    )


def _make_value_mv() -> MetricValue:
    """构造一个最小 MetricValue（gate 决策不需要真实 values）。"""
    return MetricValue(
        metric="test",
        value=0.0,
        unit="",
        raw_text="",
        chunk_id="c",
        doc_id="d",
        section_path="",
        year="2023年",
        key=ConflictKey(
            entity="公司整体",
            metric="test",
            period="2023年",
            unit="亿元",
            scope="公司整体",
        ),
    )


def _make_result(conflicts: list, coverage: float = 1.0):
    """构造 EvidenceResult 供 evidence_gate_should_refuse 测试."""
    from app.core.evidence import EvidenceResult
    return EvidenceResult(
        coverage=coverage,
        temporal_consistent=_is_temporally_consistent(conflicts),
        conflicts=conflicts,
        sources=[],
        coverage_by_year={},
    )


# ── _is_temporally_consistent 直接测试 ────────────────────────────────


def test_no_conflicts_is_consistent():
    assert _is_temporally_consistent([]) is True


def test_high_severity_makes_inconsistent():
    conflicts = [_make_conflict("high")]
    assert _is_temporally_consistent(conflicts) is False


def test_medium_severity_is_still_consistent():
    """Phase 4-C 关键变化：section_mismatch (medium) 不再让 temporal_consistent=False."""
    conflicts = [_make_conflict("medium", conflict_type="section_mismatch")]
    assert _is_temporally_consistent(conflicts) is True


def test_low_severity_is_still_consistent():
    """向后兼容：year_mismatch (low) 仍 pass."""
    conflicts = [_make_conflict("low", conflict_type="year_mismatch")]
    assert _is_temporally_consistent(conflicts) is True


def test_mixed_severity_only_high_matters():
    """混合 severity：只要有 high → False；全是 medium/low → True."""
    mixed = [
        _make_conflict("medium"),
        _make_conflict("low"),
        _make_conflict("medium"),
    ]
    assert _is_temporally_consistent(mixed) is True

    mixed_with_high = [
        _make_conflict("medium"),
        _make_conflict("high"),
        _make_conflict("low"),
    ]
    assert _is_temporally_consistent(mixed_with_high) is False


# ── evidence_gate_should_refuse 集成测试 ───────────────────────────────


def test_high_severity_triggers_refuse():
    """high severity → gate refuse."""
    result = _make_result([_make_conflict("high")])
    assert evidence_gate_should_refuse(result, threshold=0.5) is True


def test_medium_severity_does_not_trigger_refuse():
    """Phase 4-C 关键测试：medium severity → gate pass."""
    result = _make_result([_make_conflict("medium", conflict_type="section_mismatch")])
    assert evidence_gate_should_refuse(result, threshold=0.5) is False


def test_low_severity_does_not_trigger_refuse():
    """向后兼容：low severity → gate pass."""
    result = _make_result([_make_conflict("low", conflict_type="year_mismatch")])
    assert evidence_gate_should_refuse(result, threshold=0.5) is False


def test_low_coverage_still_triggers_refuse():
    """Coverage 检查仍然工作：coverage < threshold → refuse."""
    from app.core.evidence import EvidenceResult
    result = EvidenceResult(
        coverage=0.2,
        temporal_consistent=True,  # 无 conflict
        conflicts=[],
        sources=[],
        coverage_by_year={},
    )
    assert evidence_gate_should_refuse(result, threshold=0.5) is True


def test_threshold_zero_never_refuses():
    """threshold=0 时永远不拒答（gate disabled）."""
    result = _make_result([_make_conflict("high")])
    assert evidence_gate_should_refuse(result, threshold=0) is False


def test_multiple_medium_severity_no_refuse():
    """5 个 medium conflicts 也不应触发 refuse（核心修复目标）."""
    conflicts = [_make_conflict("medium", conflict_type="section_mismatch") for _ in range(5)]
    result = _make_result(conflicts)
    assert evidence_gate_should_refuse(result, threshold=0.5) is False
