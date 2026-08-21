"""Evidence gate refuse 路径回归测试。

背景：pipeline.py 的 evidence gate refuse 分支调用裸 logger.warning，但该模块
此前未定义模块级 logger —— gate 开启且进入 refuse 路径即 AttributeError。
Phase 1 修复（spec 见 docs/plans/2026-08-21-cleanup-phase1.md）。
本测试锁定修复不回退：
1. 模块级 logger 存在
2. gate refuse 时 SSE 事件流正常收尾（status → degraded → done），不再抛异常
"""

from app.config import settings
from app.core.pipeline import RAGPipeline
from app.models.schemas import ChatRequest


def test_pipeline_module_defines_logger():
    """gate refuse 分支依赖模块级 logger——必须存在且可用。"""
    from app.core import pipeline as pipeline_mod

    assert hasattr(pipeline_mod, "logger"), (
        "pipeline.py 缺少模块级 logger：evidence gate refuse 路径会 AttributeError"
    )
    assert callable(pipeline_mod.logger.warning)


class _FakeGateResult:
    """绕开 organizer 内部逻辑，只测 pipeline 分支接线。"""

    coverage = 0.5
    temporal_consistent = False
    conflicts: list = []
    sources: list = [{"chunk_id": "c1", "document_id": "d1"}]
    coverage_by_year: dict = {}


async def _fake_retrieve(*args, **kwargs):
    from app.models.schemas import RetrievedChunk

    return [RetrievedChunk(chunk_id="c1", document_id="d1", text="证据文本", score=0.9)]


async def test_gate_refuse_path_completes_without_error(monkeypatch):
    """gate 判拒答时事件流正常收尾（旧代码在此路径 AttributeError）。"""
    from app.core import pipeline as pipeline_mod

    # 把会触 DB / 真实 LLM 的所有接缝 monkeypatch 成轻量桩。
    monkeypatch.setattr(settings, "evidence_gate_enabled", True)
    monkeypatch.setattr(settings, "evidence_min_coverage", 0.99)
    monkeypatch.setattr(settings, "diagnostics_enabled", False)
    monkeypatch.setattr(
        pipeline_mod.conversation_memory,
        "get_or_create_conversation",
        lambda conv_id, user_id: "conv-1",
    )
    monkeypatch.setattr(pipeline_mod.conversation_memory, "get_history", lambda cid: [])
    monkeypatch.setattr(pipeline_mod.conversation_memory, "get_summary", lambda cid: "")
    monkeypatch.setattr(pipeline_mod.retrieval_engine, "retrieve", _fake_retrieve)
    monkeypatch.setattr(pipeline_mod.evidence_organizer, "organize", lambda **kw: None)
    monkeypatch.setattr(pipeline_mod, "build_evidence_result", lambda table: _FakeGateResult())

    events: list[str] = []
    async for ev in RAGPipeline().execute(
        ChatRequest(
            query="测试证据门控",
            knowledge_base_ids=["kb-x"],
        ),
        user_role_ids=[1],
    ):
        events.append(ev)

    joined = "".join(events)
    assert "evidence_gate_refused" in joined
    assert "event: degraded" in joined
    assert "event: done" in joined
