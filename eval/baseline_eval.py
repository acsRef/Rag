"""Baseline 15 题 verified 快测 —— regression gate，不是最终统计。

纪律（spec §9）：
- gold 一律经 eval.gold（verified_only=True，15 题集合固定）
- judge 复用 eval_sany.JUDGE_PROMPT + judge_answer（locked evaluator v1）
- 每次运行 --name 指定 baseline 名，产物隔离在 baselines/<name>/ 下
- 15 题快测是 regression gate；65 题全量才是正式结论

用法（后端须已在对应配置下运行）：
    D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-1r
    D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-2 --compare baseline-1r
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).parent
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR.parent))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from eval_sany import BASE_URL, judge_answer  # noqa: E402
from gold import iter_questions  # noqa: E402  # eval.gold —— gold 唯一入口

load_dotenv(EVAL_DIR / ".env")
load_dotenv(EVAL_DIR.parent / ".env")

OUT_ROOT = EVAL_DIR / "sany_annual_reports" / "baselines"


def login() -> str:
    resp = requests.post(
        f"{BASE_URL}/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def call_rag(query: str, token: str, kb_id: str) -> dict:
    """与 eval_sany.call_rag 相同的 SSE 解析（独立实现避免拉入其全局状态）。"""
    body = {"query": query, "knowledge_base_ids": [kb_id]}
    resp = requests.post(
        f"{BASE_URL}/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()
    answer_parts: list[str] = []
    sources: list = []
    conv_id = None
    error = None
    event_type = None
    for line in resp.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line.startswith("event: "):
            event_type = line[7:].strip()
            continue
        if line.startswith("data: ") and event_type:
            data = line[6:].strip()
            if event_type == "token":
                answer_parts.append(data)
            elif event_type == "sources":
                try:
                    sources = json.loads(data)
                except json.JSONDecodeError:
                    pass
            elif event_type == "metadata":
                try:
                    conv_id = json.loads(data).get("conversation_id", conv_id)
                except json.JSONDecodeError:
                    pass
            elif event_type == "error":
                error = data
            elif event_type == "done":
                break
    return {
        "answer": "".join(answer_parts),
        "sources": sources,
        "conversation_id": conv_id,
        "error": error,
    }


def _to_judge_dict(gq) -> dict:
    """GoldQuestion → eval_sany.judge_answer 所需的中文字段 dict。"""
    return {
        "问题": gq.question,
        "类别": gq.category,
        "难度": gq.difficulty,
        "参考答案": gq.gold_answer,
        "答案依据": gq.evidence_basis,
        "考察的RAG易错点": gq.pitfall,
        "常见错误答案": gq.common_wrong_answers,
    }


def summarize(records: list[dict]) -> dict:
    by_cat: dict[str, list] = defaultdict(list)
    for r in records:
        by_cat[r["category_prefix"]].append(r["judge_score"])
    cats = {}
    for p, v in sorted(by_cat.items()):
        scored = [x for x in v if x is not None]
        cats[p] = {
            "n": len(v),
            "mean": round(sum(scored) / max(len(scored), 1), 3),
            "acc_ge2": round(sum(1 for x in scored if x >= 2) / len(v), 3),
        }
    scored_all = [r["judge_score"] for r in records if r["judge_score"] is not None]
    n_total = len(records)
    return {
        "n": n_total,
        "mean_score_pct": round(100 * sum(scored_all) / (3 * n_total), 1) if n_total else None,
        "acc_ge2_pct": round(100 * sum(1 for s in scored_all if s >= 2) / n_total, 1)
        if n_total
        else None,
        "by_category": cats,
        "errors": [r["question_id"] for r in records if r.get("error")],
        "timestamp": datetime.now(UTC).isoformat(),
    }


def compare(summary_a: dict, summary_b: dict, name_a: str, name_b: str) -> str:
    """跨 baseline delta 表。acc 用百分点差（×100 后相减）。"""
    lines = [f"| 类别 | {name_a} acc | {name_b} acc | Δ(pp) |", "|---|---|---|---|"]
    prefixes = sorted(set(summary_a["by_category"]) | set(summary_b["by_category"]))
    for p in prefixes:
        a = summary_a["by_category"].get(p, {}).get("acc_ge2")
        b = summary_b["by_category"].get(p, {}).get("acc_ge2")
        d = ""
        if a is not None and b is not None:
            d = f"{(b - a) * 100:+.0f}"
        lines.append(f"| {p} | {a} | {b} | {d} |")
    lines.append("")
    lines.append(f"overall mean: {summary_a['mean_score_pct']}% → {summary_b['mean_score_pct']}%")
    lines.append(f"overall acc(≥2): {summary_a['acc_ge2_pct']}% → {summary_b['acc_ge2_pct']}%")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="baseline 名，如 baseline-1r / baseline-2")
    parser.add_argument("--compare", default=None, help="对比的既有 baseline 名（打印 delta 表）")
    args = parser.parse_args()

    out_dir = OUT_ROOT / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = iter_questions(verified_only=True)
    assert len(questions) == 15, f"verified 子集应为 15 题，实际 {len(questions)}"

    token = login()
    kbs_resp = requests.get(f"{BASE_URL}/api/v1/kb", headers={"Authorization": f"Bearer {token}"})
    kbs_resp.raise_for_status()
    kb_id = next((kb["id"] for kb in kbs_resp.json() if "三一重工" in kb["name"]), None)
    if not kb_id:
        print("ERROR: 找不到三一重工知识库")
        return 1

    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    base_url = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    judge_model = os.environ.get(
        "JUDGE_MODEL", os.environ.get("CHAT_MODEL", "deepseek-ai/DeepSeek-V3")
    )
    if not api_key:
        print("WARNING: SILICONFLOW_API_KEY 未设置，judge 将全部失败")

    records = []
    for i, gq in enumerate(questions, 1):
        print(
            f"[{i}/15] {gq.id} ({gq.category_prefix}) {gq.question[:36]}...",
            end=" ",
            flush=True,
        )
        rag = call_rag(gq.question, token, kb_id)
        answer = rag["answer"]
        if not answer.strip() or rag.get("error"):
            rec = {
                "question_id": gq.id,
                "category_prefix": gq.category_prefix,
                "judge_score": None,
                "judge_reason": f"RAG failed: {rag.get('error') or 'empty'}",
                "rag_answer": "",
                "error": rag.get("error") or "empty",
            }
            print("❌ RAG failed")
        else:
            time.sleep(3)
            jd = judge_answer(_to_judge_dict(gq), answer, api_key, base_url, judge_model)
            sources_count = len(rag.get("sources", []))
            if jd["score"] < 0:
                # judge 调用/解析失败（eval_sany 返回 -1）：不计分、记入 errors，
                # 不让 judge 噪声污染 gate 分数（eval methodology: judge noise P0）
                rec = {
                    "question_id": gq.id,
                    "category_prefix": gq.category_prefix,
                    "judge_score": None,
                    "judge_reason": jd["reason"],
                    "rag_answer": answer,
                    "sources_count": sources_count,
                    "error": f"judge failed: {jd['reason']}",
                }
                print(f"❌ judge: {(jd['reason'] or '')[:38]}")
            else:
                rec = {
                    "question_id": gq.id,
                    "category_prefix": gq.category_prefix,
                    "judge_score": jd["score"],
                    "judge_reason": jd["reason"],
                    "rag_answer": answer,
                    "sources_count": sources_count,
                    "error": None,
                }
                icon = {3: "✅", 2: "🔵", 1: "🟡", 0: "❌"}.get(jd["score"], "⚪")
                print(f"{icon} {jd['score']}: {(jd['reason'] or '')[:38]}")
        records.append(rec)
        (out_dir / f"{rec['question_id']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        time.sleep(3)

    summary = summarize(records)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== {args.name} ===")
    print(f"overall acc(≥2): {summary['acc_ge2_pct']}%   mean: {summary['mean_score_pct']}%")
    for p, c in summary["by_category"].items():
        print(f"  {p}: {c['acc_ge2'] * 100:.0f}% ({c['n']}题)")

    if args.compare:
        cmp_path = OUT_ROOT / args.compare / "summary.json"
        if cmp_path.exists():
            other = json.loads(cmp_path.read_text(encoding="utf-8"))
            print(f"\n--- Δ vs {args.compare} ---")
            print(compare(other, summary, args.compare, args.name))
        else:
            print(f"WARN: 找不到 {cmp_path}")

    print(f"\n产物: {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
