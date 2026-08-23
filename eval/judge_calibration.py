"""P0 Judge Calibration — 自动分层抽样 + 当前 Judge 跑完 + 落盘 + STOP。

**DO NOT**:
- 不引入 DeepEval
- 不换 judge 模型
- 不修改 evaluator / judge 实现
- 不人工核对 15 题

**DO**:
1. 分层抽样 15 题 (A=3, B=2, C=2, D=2, E=2, H=2, I=2)
2. 用现有 V3 judge (0-3 分 + reason) 跑 15 题 — 与 eval_single.py 同 judge
3. 保存完整 artifact: question / reference / RAG answer / judge score / judge reason
4. 生成 15 题的 summary + HUMAN_VERIFY.md (待人工核对)
5. ★ STOP

输出: eval/judge_calibration_v3/
- {Q01..Q15}.json — 每题完整 artifact
- summary.json — 统计
- HUMAN_VERIFY.md — 人工核对模板
"""

import asyncio
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# === env override BEFORE imports (match Baseline-1 条件) ===
os.environ.setdefault("CHAT_MODEL", "deepseek-ai/DeepSeek-V3")

# === imports after env override ===
from app.config import settings  # noqa: E402
from app.core import pipeline as pipeline_mod  # noqa: E402
from app.llm.chat import minimax_client  # noqa: E402
from app.models.schemas import ChatRequest  # noqa: E402

TESTSET_PATH = Path(__file__).parent / "sany_annual_reports" / "rag_testset.json"
OUT_DIR = Path(__file__).parent / "judge_calibration_v3"

# 分层抽样目标（per user spec）
TARGET_DIST = {
    "A": 3,  # 单文档事实
    "B": 2,  # 表格
    "C": 2,  # 跨文档
    "D": 2,  # 计算/推理
    "E": 2,  # 时序
    "H": 2,  # 错误前提
    "I": 2,  # 拒答边界
}

# === conversation_memory mock (避免在 DB 写假 user_id 的会话) ===
async def _noop(*args, **kwargs):
    return None

pipeline_mod.conversation_memory.get_or_create_conversation = (
    lambda conv_id, user_id: f"cal-{conv_id or 'new'}"
)
pipeline_mod.conversation_memory.get_history = lambda cid: []
pipeline_mod.conversation_memory.get_summary = lambda cid: ""
pipeline_mod.conversation_memory.add_message = _noop


# === Judge（与 eval_single.py 一致：0-3 分 + reason） ===

JUDGE_PROMPT = """根据参考答案判断RAG回答的准确度(0-3分)。

问题: {question}
参考答案: {reference}
RAG回答: {rag_answer}

评分标准：
- 3分：完全正确，核心信息全部覆盖，数字/单位正确
- 2分：基本正确，有小遗漏或偏差
- 1分：部分正确，有明显错误
- 0分：错误/拒答/编造

仅输出JSON: {{"score": N, "reason": "60字以内理由"}}"""


async def judge_one(question: str, reference: str, rag_answer: str) -> dict:
    """调 V3 judge，解析 0-3 分 + reason。3 次重试。"""
    if not rag_answer or not rag_answer.strip():
        return {"score": None, "reason": "RAG answer empty", "raw": ""}

    prompt = JUDGE_PROMPT.format(
        question=question, reference=reference[:500], rag_answer=rag_answer[:1000]
    )
    last_err = ""
    for attempt in range(3):
        try:
            resp = await minimax_client.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=200,
                timeout=60,
            )
            if not resp:
                last_err = "empty response"
                continue
            # 解析 JSON
            m = re.search(r"\{[^{}]*\"score\"[^{}]*\}", resp)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    score = int(parsed.get("score"))
                    reason = str(parsed.get("reason", ""))
                    return {"score": score, "reason": reason, "raw": resp}
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    last_err = f"parse error: {e}; raw={resp[:200]}"
                    continue
            last_err = f"no JSON in response: {resp[:200]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            import time as _t

            _t.sleep(2)
    return {"score": None, "reason": last_err, "raw": ""}


# === RAG runner (in-process, 类似 ablation runner) ===

_TOKEN_RE = re.compile(r'event: token\ndata: ?"?([^"\n]*)"?', re.DOTALL)


def parse_sse_answer(events: list[str]) -> str:
    joined = "".join(events)
    answer_parts = _TOKEN_RE.findall(joined)
    return "".join(answer_parts).strip()


async def run_rag(question: dict) -> tuple[list[str], str | None, str]:
    req = ChatRequest(query=question["问题"])
    events: list[str] = []
    error: str | None = None
    try:
        async for ev in pipeline_mod.RAGPipeline().execute(
            req, user_id="calibration", user_role_ids=[1]
        ):
            events.append(ev)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    answer = parse_sse_answer(events) if not error else ""
    return events, error, answer


# === Stratified sampling ===

def stratified_sample() -> list[dict]:
    """按 TARGET_DIST 分层抽样，可复现（按 id 顺序取前 N）。"""
    with open(TESTSET_PATH, encoding="utf-8") as f:
        ds = json.load(f)

    by_prefix: dict[str, list[dict]] = {}
    for q in ds["题目"]:
        prefix = q["类别"].split("-")[0].strip()
        by_prefix.setdefault(prefix, []).append(q)

    selected: list[dict] = []
    missing: list[tuple[str, int, int]] = []  # (prefix, wanted, actual)
    for prefix, count in TARGET_DIST.items():
        available = by_prefix.get(prefix, [])
        if len(available) < count:
            missing.append((prefix, count, len(available)))
            selected.extend(available)
        else:
            selected.extend(available[:count])

    print(f"Selected {len(selected)} questions:")
    by_pfx = Counter(q["类别"].split("-")[0].strip() for q in selected)
    for p, c in sorted(by_pfx.items()):
        print(f"  {p}: {c}")
    if missing:
        print(f"WARNING: missing categories: {missing}")
    print()
    return selected


# === Single-question runner ===

async def run_one(question: dict) -> dict:
    qid = question["id"]
    category = question["类别"]

    # 1. RAG generation
    events, error, answer = await run_rag(question)

    # 2. Judge
    if error or not answer.strip():
        judge_result = {"score": None, "reason": f"skipped (error={error})", "raw": ""}
    else:
        judge_result = await judge_one(
            question=question["问题"],
            reference=question["参考答案"],
            rag_answer=answer,
        )

    # 3. Citation check (deterministic)
    citations_in_answer = re.findall(r"\[(\d+)\]", answer)
    citation_count = len(citations_in_answer)
    unique_citation_ids = sorted(set(int(c) for c in citations_in_answer))

    return {
        "question_id": qid,
        "category": category,
        "category_prefix": category.split("-")[0].strip(),
        "question": question["问题"],
        "gold_answer": question["参考答案"],
        "generation_answer": answer,
        "answer_length_chars": len(answer),
        "judge_score": judge_result["score"],
        "judge_reason": judge_result["reason"],
        "judge_raw_response": judge_result["raw"],
        "citation_count": citation_count,
        "unique_citation_ids": unique_citation_ids,
        "error": error,
        "sse_event_count": len(events),
        "timestamp": datetime.now(UTC).isoformat(),
    }


# === Summary stats ===

def compute_summary(records: list[dict]) -> dict:
    n = len(records)
    by_prefix = Counter(r["category_prefix"] for r in records)
    by_score = Counter(r["judge_score"] for r in records)

    # Disagreement candidates (high attention):
    # - judge score 0/1 + we want to know if model actually answered
    # - judge score 3 (top marks) — verify correct
    # - judge_score None (parse failure)
    suspected_disagreement = []
    for r in records:
        reasons = []
        if r["judge_score"] is None:
            reasons.append("judge_parse_fail")
        if r["judge_score"] == 0 and r["generation_answer"].strip():
            reasons.append("judge_0_with_answer")
        if r["judge_score"] == 3 and not r["citation_count"]:
            reasons.append("judge_3_no_citation")
        if r["error"]:
            reasons.append(f"rag_error: {r['error']}")
        if reasons:
            suspected_disagreement.append({
                "question_id": r["question_id"],
                "reasons": reasons,
                "judge_score": r["judge_score"],
                "judge_reason": r["judge_reason"][:100] if r["judge_reason"] else "",
            })

    return {
        "n_questions": n,
        "by_category": dict(by_prefix),
        "score_distribution": {
            "0": by_score.get(0, 0),
            "1": by_score.get(1, 0),
            "2": by_score.get(2, 0),
            "3": by_score.get(3, 0),
            "null": by_score.get(None, 0),
        },
        "score_distribution_pct": {
            k: f"{v/n*100:.0f}%" for k, v in {
                "0": by_score.get(0, 0),
                "1": by_score.get(1, 0),
                "2": by_score.get(2, 0),
                "3": by_score.get(3, 0),
                "null": by_score.get(None, 0),
            }.items()
        },
        "n_errors": sum(1 for r in records if r["error"]),
        "n_suspected_disagreement": len(suspected_disagreement),
        "suspected_disagreement": suspected_disagreement,
    }


# === HUMAN_VERIFY.md 生成 ===

def generate_human_verify_md(records: list[dict]) -> str:
    """生成待人工核对模板。"""
    lines = [
        "# P0 Judge Calibration — Human Verify",
        "",
        "> **共 15 题**, 分层抽样: A=3, B=2, C=2, D=2, E=2, H=2, I=2",
        "> **你需要做的**: 对每题填 4 个字段 (human_correct / human_partial / human_refusal_correct / human_citation_correct)",
        "> **填完后**: 自动算 judge ↔ human agreement (脚本会自动处理)",
        "",
        "---",
        "",
    ]
    for r in records:
        qid = r["question_id"]
        cat = r["category"]
        lines.extend([
            f"## {qid} [{cat}]",
            "",
            f"**Question**: {r['question']}",
            "",
            f"**Gold Answer**: {r['gold_answer']}",
            "",
            f"**RAG Answer**: {r['generation_answer'][:500]}{'...' if len(r['generation_answer']) > 500 else ''}",
            "",
            f"**Judge Score**: {r['judge_score']} (0-3)",
            "",
            f"**Judge Reason**: {r['judge_reason']}",
            "",
            f"**Judge Raw Response**: `{r['judge_raw_response']}`",
            "",
            f"**Citations**: {r['citation_count']} 个, IDs: {r['unique_citation_ids']}",
            "",
            "**Human Verify (填空)**：",
            "- [ ] human_correct: true / false",
            "- [ ] human_partial: true / false",
            "- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)",
            "- [ ] human_citation_correct: true / false / N/A",
            "",
            "**Notes**: (free-form 备注)",
            "",
            "---",
            "",
        ])
    lines.extend([
        "## 填完后请运行",
        "",
        "```bash",
        "D:/miniConda/envs/rag/python.exe eval/aggregate_judge_agreement.py \\",
        "  --human <path-to-filled-md>",
        "```",
        "",
        "(aggregate_judge_agreement.py 待实现 — 解析 human_verify.md 中的 [ ] 标记)",
        "",
    ])
    return "\n".join(lines)


# === Main ===

async def amain():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    selected = stratified_sample()

    records = []
    for q in selected:
        print(f"=== {q['id']} ({q['类别']}) ===", flush=True)
        r = await run_one(q)

        # Save per-question artifact
        out_path = OUT_DIR / f"{r['question_id']}.json"
        out_path.write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        score = r["judge_score"]
        score_s = "?" if score is None else str(score)
        err_s = f" ERR={r['error']}" if r["error"] else ""
        print(
            f"  judge={score_s} cite={r['citation_count']}{err_s}"
        )

        records.append(r)

    # Save summary
    summary = compute_summary(records)
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Generate HUMAN_VERIFY.md
    human_md = generate_human_verify_md(records)
    (OUT_DIR / "HUMAN_VERIFY.md").write_text(human_md, encoding="utf-8")

    # Print summary
    print("\n" + "=" * 60)
    print("★ STOP — Judge Calibration 自动部分完成, 等用户手验")
    print("=" * 60)
    print(f"\nOutput: {OUT_DIR}/")
    print(f"  Per-question: {len(records)} JSON files")
    print(f"  Summary: summary.json")
    print(f"  Human verify template: HUMAN_VERIFY.md")
    print()
    print("Score distribution:")
    for k, v in summary["score_distribution"].items():
        pct = summary["score_distribution_pct"].get(k, "?")
        print(f"  {k}: {v} ({pct})")
    print()
    print(f"Suspected disagreement: {summary['n_suspected_disagreement']} 题")
    for s in summary["suspected_disagreement"]:
        print(f"  {s['question_id']}: {s['reasons']}")


if __name__ == "__main__":
    asyncio.run(amain())
