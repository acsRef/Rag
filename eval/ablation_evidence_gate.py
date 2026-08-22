"""B1 Stage 2 runner — Evidence Gate value ablation.

详见 docs/plans/2026-08-21-b1-stage2-ablation.md。

直接调用 RAGPipeline.execute() 而非 HTTP backend；逐题 monkeypatch
settings.evidence_gate_enabled / evidence_min_coverage；用 Qwen3-8B 作 judge
（与 chat 同 provider，无需额外凭据）。

用法：
    D:/miniConda/envs/rag/python.exe eval/ablation_evidence_gate.py --limit 5
    D:/miniConda/envs/rag/python.exe eval/ablation_evidence_gate.py --limit 65
"""

import argparse
import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

# === env override per plan §2.2 (必须在 import app.config 之前) ===
os.environ.setdefault("CHAT_MODEL", "Qwen/Qwen3-8B")

# === imports after env override ===
from app.config import settings  # noqa: E402
from app.core import evidence as ev_mod  # noqa: E402
from app.core import pipeline as pipeline_mod  # noqa: E402
from app.models.schemas import ChatRequest  # noqa: E402

TESTSET_PATH = Path(__file__).parent / "sany_annual_reports" / "rag_testset.json"

CONFIGS = [
    {"name": "off", "gate_enabled": False, "threshold": None},
    {"name": "t05", "gate_enabled": True, "threshold": 0.5},
    {"name": "t07", "gate_enabled": True, "threshold": 0.7},
    {"name": "t09", "gate_enabled": True, "threshold": 0.9},
]

# 类别前缀 → should_answer（per plan §2.4）
SHOULD_ANSWER_BY_PREFIX = {
    "A": True,
    "B": True,
    "C": True,
    "D": True,
    "E": True,
    "G": True,
    "J": True,
    "H": True,
    "I": False,
}


def get_should_answer(category: str) -> bool:
    prefix = category.split("-")[0].strip() if category else ""
    return SHOULD_ANSWER_BY_PREFIX.get(prefix, True)


def get_category_prefix(category: str) -> str:
    return category.split("-")[0].strip() if category else ""


# ── Gate capture ──────────────────────────────────────
_gate_capture: dict = {}


def _wrap_gate_should_refuse(orig_fn):
    """Wrap evidence_gate_should_refuse to capture last EvidenceResult input."""

    def wrapped(result, threshold):
        _gate_capture["last_result"] = {
            "coverage": result.coverage,
            "temporal_consistent": result.temporal_consistent,
            "conflicts_count": len(result.conflicts or []),
            "sources_count": len(result.sources or []),
            "coverage_by_year": dict(result.coverage_by_year or {}),
            "threshold": threshold,
        }
        return orig_fn(result, threshold)

    return wrapped


_orig_gate_fn = ev_mod.evidence_gate_should_refuse
_wrapped_gate_fn = _wrap_gate_should_refuse(_orig_gate_fn)
# pipeline.py 用 `from app.core.evidence import evidence_gate_should_refuse` 引入了模块级绑定；
# 重新赋值 ev_mod 上的属性不会改 pipeline 的本地引用，所以两边都打补丁。
ev_mod.evidence_gate_should_refuse = _wrapped_gate_fn
pipeline_mod.evidence_gate_should_refuse = _wrapped_gate_fn


def reset_gate_capture() -> None:
    _gate_capture.clear()


# ── SSE 解析 ──────────────────────────────────────────
_STATUS_RE = re.compile(r"event: status\ndata: (\{.*?\})\n\n", re.DOTALL)
_DEGRADED_RE = re.compile(r"event: degraded\ndata: (\{.*?\})\n\n", re.DOTALL)
_TOKEN_RE = re.compile(r'event: token\ndata: ?"?([^"\n]*)"?', re.DOTALL)


def parse_sse_events(events: list[str]) -> dict:
    joined = "".join(events)
    status_match = _STATUS_RE.search(joined)
    status_payload = None
    if status_match:
        try:
            status_payload = json.loads(status_match.group(1))
        except json.JSONDecodeError:
            pass
    degraded_match = _DEGRADED_RE.search(joined)
    degraded_payload = None
    if degraded_match:
        try:
            degraded_payload = json.loads(degraded_match.group(1))
        except json.JSONDecodeError:
            pass
    answer_parts = _TOKEN_RE.findall(joined)
    answer = "".join(answer_parts).strip()
    refused = "evidence_gate_refused" in joined
    refusal_reason = None
    if refused and status_payload:
        refusal_reason = status_payload.get("reason")
    return {
        "answer": answer,
        "gate_refused": refused,
        "refusal_reason": refusal_reason,
        "degraded": degraded_payload,
        "status": status_payload,
    }


# ── LLM judge ─────────────────────────────────────────
async def judge_one(record: dict) -> bool | None:
    """用 chat model 判 is_correct。gate refused / error 时返回 None。"""
    if record["gate_refused"]:
        return None
    if record.get("error"):
        return None
    if not record["generation_answer"].strip():
        return None
    from app.llm.chat import minimax_client  # 实际 provider 按 settings.chat_provider

    judge_prompt = (
        "判断下面模型对问题的回答是否正确。仅回答 'correct' 或 'incorrect'，"
        "不要解释，不要其他文字。\n\n"
        f"问题：{record['question_id']} {record['category']}\n"
        f"参考答案：{record['gold_answer']}\n"
        f"模型回答：{record['generation_answer'][:800]}\n\n"
    )
    try:
        resp = await minimax_client.chat(
            [{"role": "user", "content": judge_prompt}],
            tag="ablation_judge",
            max_tokens=8,
            timeout=20,
        )
        if not resp:
            return None
        text = resp.strip().lower()
        if "correct" in text and "incorrect" not in text:
            return True
        if "incorrect" in text:
            return False
        return None
    except Exception:
        return None


# ── 单题执行 ──────────────────────────────────────────
async def run_one(question: dict, config: dict) -> dict:
    req = ChatRequest(query=question["问题"])
    reset_gate_capture()
    t0 = time.monotonic()
    events: list[str] = []
    error: str | None = None
    try:
        async for ev in pipeline_mod.RAGPipeline().execute(
            req,
            user_id="ablation",
            user_role_ids=[1],
        ):
            events.append(ev)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    latency_ms = int((time.monotonic() - t0) * 1000)
    parsed = parse_sse_events(events) if not error else {}
    gate = _gate_capture.get("last_result", {})
    return {
        "question_id": question["id"],
        "category": question["类别"],
        "category_prefix": get_category_prefix(question["类别"]),
        "config": {
            "gate_enabled": config["gate_enabled"],
            "threshold": config["threshold"],
        },
        "coverage": gate.get("coverage"),
        "temporal_consistent": gate.get("temporal_consistent"),
        "conflicts_count": gate.get("conflicts_count"),
        "sources_count": gate.get("sources_count"),
        "coverage_by_year": gate.get("coverage_by_year") or {},
        "threshold": config["threshold"] if config["gate_enabled"] else None,
        "gate_refused": parsed.get("gate_refused", False),
        "refusal_reason": parsed.get("refusal_reason"),
        "generation_answer": parsed.get("answer", ""),
        "gold_answer": question["参考答案"],
        "is_correct": None,
        "should_answer": get_should_answer(question["类别"]),
        "latency_ms": latency_ms,
        "sse_event_count": len(events),
        "sse_has_evidence_refused": parsed.get("gate_refused", False),
        "sse_has_degraded": parsed.get("degraded") is not None,
        "error": error,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── 聚合 ──────────────────────────────────────────────
def aggregate(records: list[dict], config: dict) -> dict:
    valid = [r for r in records if not r.get("error")]
    n = len(valid)
    n_should_answer = sum(1 for r in valid if r["should_answer"])
    n_should_refuse = sum(1 for r in valid if not r["should_answer"])
    n_correct_should_answer = sum(
        1 for r in valid if r["should_answer"] and r.get("is_correct") is True
    )
    n_refused = sum(1 for r in valid if r["gate_refused"])
    n_should_ans_refused = sum(1 for r in valid if r["should_answer"] and r["gate_refused"])
    accuracy = n_correct_should_answer / n_should_answer if n_should_answer else 0.0
    refusal_rate = n_refused / n if n else 0.0
    false_refusal_rate = n_should_ans_refused / n_should_answer if n_should_answer else 0.0
    latencies = sorted(r["latency_ms"] for r in valid)
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = (
        latencies[int(len(latencies) * 0.95)]
        if len(latencies) >= 2
        else (latencies[-1] if latencies else 0)
    )
    return {
        "config": {
            "gate_enabled": config["gate_enabled"],
            "threshold": config["threshold"],
        },
        "n_questions": n,
        "n_should_answer": n_should_answer,
        "n_should_refuse": n_should_refuse,
        "metrics": {
            "accuracy": round(accuracy, 4),
            "refusal_rate": round(refusal_rate, 4),
            "false_refusal_rate": round(false_refusal_rate, 4),
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
        },
    }


# ── Pairwise ──────────────────────────────────────────
def pairwise_transition(off_records, t_records) -> dict:
    off_by_q = {r["question_id"]: r for r in off_records}
    t_by_q = {r["question_id"]: r for r in t_records}

    def state(r):
        if r["gate_refused"]:
            return "refused"
        if r.get("is_correct") is True:
            return "correct"
        if r.get("error"):
            return "wrong"  # treat error as wrong
        return "wrong"

    transitions = {
        "stable_correct": 0,
        "regression": 0,
        "false_refusal": 0,
        "recovery": 0,
        "stable_wrong": 0,
        "replaced_with_refusal": 0,
        "unnecessary_refusal_recovery": 0,
        "regression_from_refusal": 0,
        "stable_refused": 0,
    }
    canonical_map = {
        ("correct", "correct"): "stable_correct",
        ("correct", "wrong"): "regression",
        ("correct", "refused"): "false_refusal",
        ("wrong", "correct"): "recovery",
        ("wrong", "wrong"): "stable_wrong",
        ("wrong", "refused"): "replaced_with_refusal",
        ("refused", "correct"): "unnecessary_refusal_recovery",
        ("refused", "wrong"): "regression_from_refusal",
        ("refused", "refused"): "stable_refused",
    }
    common_qs = sorted(set(off_by_q) & set(t_by_q))
    for q in common_qs:
        s_off = state(off_by_q[q])
        s_t = state(t_by_q[q])
        key = canonical_map.get((s_off, s_t))
        if key:
            transitions[key] += 1

    recovery = transitions["recovery"]
    replaced = transitions["replaced_with_refusal"]
    if recovery + replaced > 0:
        ratio = recovery / (recovery + replaced)
    else:
        ratio = None
    return {
        "transitions": transitions,
        "recovery_ratio": round(ratio, 4) if ratio is not None else None,
        "n_common_questions": len(common_qs),
    }


# ── 三轴判定 ──────────────────────────────────────────
ACCEPTANCE = {
    "accuracy_min_improvement_pp": 2.0,
    "false_refusal_rate_max": 0.03,
    "latency_p95_max_overhead_pct": 20.0,
}


def three_axis_judge(off_agg: dict, t_agg: dict) -> dict:
    """Return per-axis pass + overall, all relative to off."""
    off_acc = off_agg["metrics"]["accuracy"]
    off_fr = off_agg["metrics"]["false_refusal_rate"]
    off_p95 = off_agg["metrics"]["latency_p95_ms"]
    t_acc = t_agg["metrics"]["accuracy"]
    t_fr = t_agg["metrics"]["false_refusal_rate"]
    t_p95 = t_agg["metrics"]["latency_p95_ms"]
    acc_improvement = (t_acc - off_acc) * 100
    fr = t_fr  # already absolute rate
    p95_overhead_pct = ((t_p95 - off_p95) / off_p95 * 100) if off_p95 > 0 else 0.0
    passes_acc = acc_improvement >= ACCEPTANCE["accuracy_min_improvement_pp"]
    passes_fr = fr <= ACCEPTANCE["false_refusal_rate_max"]
    passes_lat = p95_overhead_pct <= ACCEPTANCE["latency_p95_max_overhead_pct"]
    return {
        "accuracy_improvement_pp": round(acc_improvement, 3),
        "false_refusal_rate": round(fr, 4),
        "p95_latency_overhead_pct": round(p95_overhead_pct, 2),
        "thresholds": ACCEPTANCE,
        "passes_accuracy": passes_acc,
        "passes_false_refusal": passes_fr,
        "passes_latency": passes_lat,
        "overall_pass": passes_acc and passes_fr and passes_lat,
    }


# ── main ──────────────────────────────────────────────
async def amain(args):
    with open(TESTSET_PATH, encoding="utf-8") as f:
        ds = json.load(f)
    items = ds["题目"][: args.limit]
    selected = [c for c in CONFIGS if c["name"] in args.configs]

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out_dir = Path(f"eval/ablation/{timestamp}")
    per_q_dir = out_dir / "per_question"
    agg_dir = out_dir / "aggregate"
    pair_dir = out_dir / "pairwise"
    for d in [per_q_dir, agg_dir, pair_dir]:
        d.mkdir(parents=True, exist_ok=True)

    records_by_config: dict[str, list[dict]] = {}
    for config in selected:
        print(
            f"\n=== config={config['name']} gate={config['gate_enabled']} "
            f"threshold={config['threshold']} ===",
            flush=True,
        )
        settings.evidence_gate_enabled = config["gate_enabled"]
        if config["threshold"] is not None:
            settings.evidence_min_coverage = config["threshold"]

        records: list[dict] = []
        for q in items:
            print(f"  [{config['name']}] {q['id']} ...", end=" ", flush=True)
            r = await run_one(q, config)
            if args.judge:
                r["is_correct"] = await judge_one(r)
            records.append(r)
            (per_q_dir / f"{config['name']}_{q['id']}.json").write_text(
                json.dumps(r, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            status = "ERR" if r.get("error") else ("REFUSED" if r["gate_refused"] else "answered")
            correct = r["is_correct"]
            correct_s = "?" if correct is None else ("Y" if correct else "N")
            print(
                f"{status} correct={correct_s} "
                f"cov={r.get('coverage')} "
                f"tc={r.get('temporal_consistent')} "
                f"{r['latency_ms']}ms"
            )

        records_by_config[config["name"]] = records
        agg = aggregate(records, config)
        (agg_dir / f"{config['name']}_summary.json").write_text(
            json.dumps(agg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        m = agg["metrics"]
        print(
            f"  aggregate: accuracy={m['accuracy']:.2%} "
            f"refusal={m['refusal_rate']:.2%} "
            f"false_refusal={m['false_refusal_rate']:.2%} "
            f"p50={m['latency_p50_ms']}ms p95={m['latency_p95_ms']}ms"
        )

    # pairwise vs off
    if "off" in records_by_config:
        for tname in ["t05", "t07", "t09"]:
            if tname in records_by_config:
                pw = pairwise_transition(records_by_config["off"], records_by_config[tname])
                (pair_dir / f"off_vs_{tname}.json").write_text(
                    json.dumps(pw, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(
                    f"\npairwise off_vs_{tname}: recovery={pw['transitions']['recovery']} "
                    f"replaced={pw['transitions']['replaced_with_refusal']} "
                    f"ratio={pw['recovery_ratio']}"
                )

    # decision
    decision: dict = {"per_config": {}, "final": None}
    if "off" in records_by_config:
        off_agg = aggregate(records_by_config["off"], CONFIGS[0])
        for tname in ["t05", "t07", "t09"]:
            if tname not in records_by_config:
                continue
            t_cfg = next(c for c in CONFIGS if c["name"] == tname)
            t_agg = aggregate(records_by_config[tname], t_cfg)
            axis = three_axis_judge(off_agg, t_agg)
            decision["per_config"][tname] = {
                "three_axis": axis,
                "aggregate_off": off_agg["metrics"],
                "aggregate_t": t_agg["metrics"],
            }
        # outcome
        pass_set = {
            t: decision["per_config"][t]["three_axis"]["overall_pass"]
            for t in decision["per_config"]
        }
        if any(pass_set.values()):
            t_pass = [t for t, v in pass_set.items() if v]
            decision["final"] = f"REFACTOR + ENABLE candidate: {t_pass}"
        else:
            decision["final"] = "DELETE (no config passes 3-axis)"
    (out_dir / "decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nDone. Outputs: {out_dir}")
    print(f"Decision: {decision['final']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--configs", nargs="+", default=["off", "t05", "t07", "t09"])
    parser.add_argument("--judge", action="store_true", default=True)
    parser.add_argument("--no-judge", dest="judge", action="store_false")
    args = parser.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
