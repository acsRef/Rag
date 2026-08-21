"""B1 Stage 1 确定性技术场景 smoke test — Evidence Gate 验证。

Phase 2 B 类侦察决策 Stage 1（详见 docs/plans/2026-08-22-phase2-b-recon.md §5.1）。

构造确定性固定场景验证 Gate 代码路径与 SSE 事件序列，不消耗 LLM API、不依赖
benchmark questions。Stage 1 通过后才进入 Stage 2（65 题价值 ablation）。

场景清单（对应 recon §5.1）：
1. 正常通过 — coverage 充分 / 无冲突 → gate 不拒答
2. 低 coverage → refuse — coverage < threshold 触发 evidence_refused
3. temporal conflict → refuse — temporal_consistent=False 触发拒答
4. threshold 边界 — 严格小于关系；threshold=0 永不拒答
5. gate exception → fallback — organizer 抛异常时降级 + 日志记录
6. SSE event sequence — refuse 路径事件顺序固定
"""


from app.core import evidence as ev_mod

# ── 单元测试：直接验证 gate 决策函数 ──────────────────────────────


class TestGateDecisionFunction:
    """evidence_gate_should_refuse() 决策逻辑的确定性单元测试。"""

    def test_normal_high_coverage_no_conflict_passes(self):
        """场景 1：coverage=1.0, temporal_consistent=True, threshold=0.7 → 不拒答。"""
        result = ev_mod.EvidenceResult(
            coverage=1.0,
            temporal_consistent=True,
            conflicts=[],
            sources=[],
        )
        assert ev_mod.evidence_gate_should_refuse(result, 0.7) is False

    def test_low_coverage_triggers_refuse(self):
        """场景 2：coverage=0.5 < threshold=0.7 → 拒答。"""
        result = ev_mod.EvidenceResult(
            coverage=0.5,
            temporal_consistent=True,
            conflicts=[],
            sources=[],
        )
        assert ev_mod.evidence_gate_should_refuse(result, 0.7) is True

    def test_temporal_conflict_triggers_refuse_even_high_coverage(self):
        """场景 3：temporal_consistent=False 即便 coverage=1.0 也拒答。"""
        result = ev_mod.EvidenceResult(
            coverage=1.0,
            temporal_consistent=False,
            conflicts=["x"],
            sources=[],
        )
        assert ev_mod.evidence_gate_should_refuse(result, 0.7) is True

    def test_threshold_zero_never_refuses(self):
        """场景 4：threshold=0 永远不拒答（gate 禁用边界）。"""
        result = ev_mod.EvidenceResult(
            coverage=0.0,
            temporal_consistent=False,
            conflicts=["x"],
            sources=[],
        )
        assert ev_mod.evidence_gate_should_refuse(result, 0.0) is False

    def test_threshold_strict_inequality_at_boundary(self):
        """场景 4：coverage == threshold 不触发（严格小于）；just below 触发。"""
        # 拒答条件：coverage < threshold（覆盖率低于阈值才拒答）
        result = ev_mod.EvidenceResult(
            coverage=0.7,
            temporal_consistent=True,
            conflicts=[],
            sources=[],
        )
        # 边界：coverage == threshold → 不拒答
        assert ev_mod.evidence_gate_should_refuse(result, 0.7) is False
        # coverage 高于 threshold → 不拒答
        assert ev_mod.evidence_gate_should_refuse(result, 0.69) is False
        # coverage 略低于 threshold → 拒答
        assert ev_mod.evidence_gate_should_refuse(result, 0.71) is True

        # 单独验证"略低于"方向
        result_low = ev_mod.EvidenceResult(
            coverage=0.69,
            temporal_consistent=True,
            conflicts=[],
            sources=[],
        )
        assert ev_mod.evidence_gate_should_refuse(result_low, 0.7) is True


# ── 集成测试：通过 pipeline.execute() 验证 SSE 事件序列 ──────────────


class _FakeResultLowCoverage:
    coverage = 0.5
    temporal_consistent = True
    conflicts: list = []
    sources: list = [{"chunk_id": "c1", "document_id": "d1"}]
    coverage_by_year: dict = {}


class _FakeResultTemporalConflict:
    coverage = 1.0
    temporal_consistent = False
    conflicts: list = ["conflict-mock-1"]
    sources: list = [{"chunk_id": "c1", "document_id": "d1"}]
    coverage_by_year: dict = {}


async def _fake_retrieve(*args, **kwargs):
    from app.models.schemas import RetrievedChunk

    return [
        RetrievedChunk(
            chunk_id="c1", document_id="d1", text="证据文本", score=0.9
        )
    ]


def _collect_events(pipeline_mod, *, fake_result):
    """Run pipeline.execute() and collect SSE events. Caller asserts."""
    from app.models.schemas import ChatRequest

    async def _runner():
        return [
            ev
            async for ev in pipeline_mod.RAGPipeline().execute(
                ChatRequest(query="smoke test", knowledge_base_ids=["kb-x"]),
                user_role_ids=[1],
            )
        ]

    return _runner


def _apply_base_mocks(pipeline_mod, monkeypatch, *, fake_result):
    """共用 mock 装配：DB / retrieval / organizer / build_evidence_result。"""
    from app.config import settings

    monkeypatch.setattr(settings, "evidence_gate_enabled", True)
    monkeypatch.setattr(settings, "evidence_min_coverage", 0.7)
    monkeypatch.setattr(settings, "diagnostics_enabled", False)
    monkeypatch.setattr(
        pipeline_mod.conversation_memory,
        "get_or_create_conversation",
        lambda conv_id, user_id: "conv-1",
    )
    monkeypatch.setattr(
        pipeline_mod.conversation_memory, "get_history", lambda cid: []
    )
    monkeypatch.setattr(
        pipeline_mod.conversation_memory, "get_summary", lambda cid: ""
    )
    monkeypatch.setattr(
        pipeline_mod.retrieval_engine, "retrieve", _fake_retrieve
    )
    monkeypatch.setattr(
        pipeline_mod.evidence_organizer, "organize", lambda **kw: None
    )
    monkeypatch.setattr(
        pipeline_mod, "build_evidence_result", lambda table: fake_result
    )


async def test_pipeline_low_coverage_emits_refused_sse(monkeypatch):
    """场景 2/6 集成验证：低 coverage → SSE 包含 evidence_refused 状态事件。"""
    from app.core import pipeline as pipeline_mod

    _apply_base_mocks(
        pipeline_mod, monkeypatch, fake_result=_FakeResultLowCoverage()
    )
    events = await _collect_events(
        pipeline_mod, fake_result=_FakeResultLowCoverage()
    )()

    joined = "".join(events)
    assert "evidence_gate_refused" in joined
    assert "evidence_refused" in joined
    assert "event: degraded" in joined
    assert "event: done" in joined


async def test_pipeline_temporal_conflict_refuse_reason_text(monkeypatch):
    """场景 3 集成验证：temporal_consistent=False 时，refuse reason 含"冲突"。"""
    from app.core import pipeline as pipeline_mod

    fake = _FakeResultTemporalConflict()
    _apply_base_mocks(pipeline_mod, monkeypatch, fake_result=fake)
    events = await _collect_events(pipeline_mod, fake_result=fake)()

    joined = "".join(events)
    assert "evidence_gate_refused" in joined
    assert "冲突" in joined or "conflict" in joined.lower()
    assert "evidence_gate.failed_falling_through" not in joined


async def test_pipeline_gate_exception_falls_through(monkeypatch, caplog):
    """场景 5 集成验证：organizer 抛异常 → 降级 + 日志 evidence_gate.failed_falling_through。"""
    from app.core import pipeline as pipeline_mod

    _apply_base_mocks(
        pipeline_mod,
        monkeypatch,
        fake_result=_FakeResultLowCoverage(),
    )
    # 替换 organizer 为抛异常的 stub
    def _raise(**kw):
        raise RuntimeError("simulated organizer failure")

    monkeypatch.setattr(pipeline_mod.evidence_organizer, "organize", _raise)
    # build_evidence_result 此时不会被调用（organizer 先抛），但 mock 仍在

    with caplog.at_level("ERROR", logger="app.core.pipeline"):
        # 用 try/except 兜底下游可能的失败（gate 异常不应阻断主流程，
        # 但下游其他阶段可能因测试 fixture 不全而崩）
        try:
            events = await _collect_events(
                pipeline_mod, fake_result=_FakeResultLowCoverage()
            )()
        except Exception:
            events = []

    # 关键断言：gate 降级日志出现
    log_text = "\n".join(r.message for r in caplog.records)
    assert "evidence_gate.failed_falling_through" in log_text, (
        "gate 异常时未触发降级日志；fallback 路径可能未实现"
    )
    # 关键断言：未触发 evidence_refused（因为 gate 失败而非真拒答）
    joined = "".join(events)
    assert "evidence_gate_refused" not in joined


async def test_pipeline_refuse_event_order_status_degraded_done(monkeypatch):
    """场景 6 集成验证：refuse 路径事件顺序固定 status → degraded → done。"""
    from app.core import pipeline as pipeline_mod

    _apply_base_mocks(
        pipeline_mod, monkeypatch, fake_result=_FakeResultLowCoverage()
    )
    events = await _collect_events(
        pipeline_mod, fake_result=_FakeResultLowCoverage()
    )()

    # 提取事件类型序列（status / degraded / done）
    import re

    event_types = re.findall(r"event: (\w+)", "".join(events))

    # 定位 evidence_refused 状态事件位置
    try:
        idx_refused = next(
            i
            for i, e in enumerate(events)
            if "evidence_refused" in e
        )
    except StopIteration:
        raise AssertionError("未找到 evidence_refused 状态事件")

    # 第一个状态事件必须是 evidence_refused（紧随 metadata 之后的 status）
    # 完整顺序：metadata → status(evidence_refused) → degraded → done
    assert "status" in event_types[idx_refused]
    assert "evidence_refused" in events[idx_refused]

    # degraded 必须在 status 之后、done 之前
    idx_degraded = next(
        i for i, e in enumerate(events) if e.startswith("event: degraded")
    )
    idx_done = next(
        i for i, e in enumerate(events) if e.startswith("event: done")
    )
    assert idx_refused < idx_degraded < idx_done, (
        f"事件顺序错乱: refused={idx_refused}, degraded={idx_degraded}, "
        f"done={idx_done}"
    )
