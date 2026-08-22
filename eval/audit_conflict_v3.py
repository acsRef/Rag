"""ConflictDetector 审计 — V3 + gate=on, 捕获完整 conflicts 列表。

复用 ablation runner 的 conversation_memory mock 模式，但 capture 的不是
gate decision 而是把每个 EvidenceResult 全字段落盘。

Usage:
    PYTHONPATH=. CHAT_MODEL=deepseek-ai/DeepSeek-V3 \\
      D:/miniConda/envs/rag/python.exe eval/audit_conflict_v3.py
"""
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

# === imports after env override ===
from app.config import settings  # noqa: E402
from app.core import evidence as ev_mod  # noqa: E402
from app.core import pipeline as pipeline_mod  # noqa: E402
from app.models.schemas import ChatRequest  # noqa: E402

TESTSET_PATH = Path(__file__).parent / "sany_annual_reports" / "rag_testset.json"
OUT_DIR = Path(__file__).parent / "ablation" / "audit_conflict_v3"

# ── Conversation memory mock ─────────────────────────────────────────
async def _noop(*args, **kwargs):
    return None

pipeline_mod.conversation_memory.get_or_create_conversation = (
    lambda conv_id, user_id: f"audit-{conv_id or 'new'}"
)
pipeline_mod.conversation_memory.get_history = lambda cid: []
pipeline_mod.conversation_memory.get_summary = lambda cid: ""
pipeline_mod.conversation_memory.add_message = _noop


# ── EvidenceResult capture ──────────────────────────────────────────
# Wrap evidence_gate_should_refuse to capture the *last* EvidenceResult
# passed in by pipeline (we don't care about the gate decision, only the input).

_captured: dict = {}
_orig_fn = ev_mod.evidence_gate_should_refuse


def _wrap(orig):
    def wrapped(result, threshold):
        conflicts_detail = []
        for c in (result.conflicts or []):
            conflicts_detail.append({
                "metric": c.metric,
                "conflict_type": c.conflict_type,
                "severity": c.severity,
                "resolution_hint": c.resolution_hint,
                "values": [
                    {
                        "metric": v.metric,
                        "value": v.value,
                        "unit": v.unit,
                        "raw_text": v.raw_text,
                        "chunk_id": v.chunk_id,
                        "doc_id": v.doc_id,
                        "section_path": v.section_path,
                        "year": v.year,
                    }
                    for v in c.values
                ],
            })
        _captured["last"] = {
            "coverage": result.coverage,
            "temporal_consistent": result.temporal_consistent,
            "conflicts": conflicts_detail,
            "conflicts_count": len(conflicts_detail),
            "sources": result.sources,
            "coverage_by_year": dict(result.coverage_by_year or {}),
            "threshold": threshold,
        }
        return orig(result, threshold)

    return wrapped


_wrapped = _wrap(_orig_fn)
# 两边都打补丁（pipeline.py 用 `from app.core.evidence import ...` 模块级绑定）
ev_mod.evidence_gate_should_refuse = _wrapped
pipeline_mod.evidence_gate_should_refuse = _wrapped


def reset_capture() -> None:
    _captured.clear()


async def run_one(question: dict) -> dict:
    req = ChatRequest(query=question["问题"])
    reset_capture()
    events: list[str] = []
    error: str | None = None
    captured_at_end: dict = {}
    try:
        async for ev in pipeline_mod.RAGPipeline().execute(
            req,
            user_id="audit",
            user_role_ids=[1],
        ):
            events.append(ev)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    captured_at_end = dict(_captured)  # snapshot
    return {
        "question_id": question["id"],
        "category": question["类别"],
        "question": question["问题"],
        "gold_answer": question["参考答案"],
        "captured": captured_at_end.get("last"),
        "sse_event_count": len(events),
        "error": error,
    }


async def amain():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(TESTSET_PATH, encoding="utf-8") as f:
        ds = json.load(f)
    items = ds["题目"][:10]

    # Force gate on with low coverage threshold (we want to capture conflicts,
    # not be refused by coverage)
    settings.evidence_gate_enabled = True
    settings.evidence_min_coverage = 0.1

    summary = []
    for q in items:
        print(f"\n=== {q['id']} ===", flush=True)
        rec = await run_one(q)
        cap = rec["captured"] or {}
        tc = cap.get("temporal_consistent")
        n_conf = cap.get("conflicts_count", 0)
        print(f"  temporal_consistent={tc}  conflicts={n_conf}")
        if cap.get("conflicts"):
            types = {}
            for c in cap["conflicts"]:
                t = c["conflict_type"]
                types[t] = types.get(t, 0) + 1
            print(f"  conflict_types={types}")
            for c in cap["conflicts"]:
                vals_summary = [
                    f"year={v.get('year') or '?'} value={v.get('value')}{v.get('unit')}"
                    for v in c["values"][:4]
                ]
                print(f"    {c['conflict_type']}/{c['severity']} metric={c['metric']}")
                for vs in vals_summary:
                    print(f"      - {vs}")

        out_path = OUT_DIR / f"{q['id']}.json"
        out_path.write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary.append({
            "question_id": q["id"],
            "temporal_consistent": tc,
            "conflicts_count": n_conf,
            "conflict_types": (
                list({c["conflict_type"] for c in cap.get("conflicts", [])})
                if cap else []
            ),
        })

    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== Summary ===")
    for s in summary:
        print(f"  {s['question_id']}: tc={s['temporal_consistent']} "
              f"conflicts={s['conflicts_count']} types={s['conflict_types']}")


if __name__ == "__main__":
    asyncio.run(amain())
