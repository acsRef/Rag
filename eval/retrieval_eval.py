"""Retrieval-only 评测脚本：Query → /api/v1/retrieve → metrics。

不调 LLM，只看检索层是否能把 gold 文档/chunk 召回上来。配合 eval/metrics.py：
- Recall@5 / Recall@10 / MRR / Hit Rate（gold_documents 在 testset 时计算）
- 无 gold 标注时退化为"打印 top1 + retrieved_count"的 smoke 输出

用法：
    D:/miniConda/envs/rag/python.exe eval/retrieval_eval.py --tier smoke
    D:/miniConda/envs/rag/python.exe eval/retrieval_eval.py --tier regression --top-k 10

环境：
    - 后端必须已起（默认 http://localhost:8000）；用 RAGENT_BASE_URL 覆盖
    - admin/admin123 默认账号；调 RAGENT_KB_NAME_HINT 改 KB 名称匹配关键词
    - 结果写到 eval/sany_annual_reports/retrieval_results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# 让 eval/ 作为包导入 metrics（同目录运行 OK；项目根目录运行也 OK）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.metrics import compute_all  # noqa: E402

BASE_URL = os.environ.get("RAGENT_BASE_URL", "http://localhost:8000")
TESTSET_PATH = Path(__file__).parent / "sany_annual_reports" / "rag_testset.json"
TIERS_PATH = Path(__file__).parent / "sany_annual_reports" / "tiers.json"
RESULT_PATH = Path(__file__).parent / "sany_annual_reports" / "retrieval_results.json"
DEFAULT_KB_HINT = os.environ.get("RAGENT_KB_NAME_HINT", "三一重工")
DEFAULT_USER = os.environ.get("RAGENT_USERNAME", "admin")
DEFAULT_PASS = os.environ.get("RAGENT_PASSWORD", "admin123")


# ── I/O helpers ────────────────────────────────────────────


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _select_questions(testset: dict, tier: str, tiers_cfg: dict) -> list[dict]:
    questions = testset.get("题目") or []
    q_by_id = {q["id"]: q for q in questions}

    if tier == "full":
        return list(questions)
    if tier not in tiers_cfg:
        sys.exit(f"ERROR: 未知 tier '{tier}'；可用 {list(tiers_cfg.keys())}")

    raw = tiers_cfg[tier]
    if isinstance(raw, str) and raw == "all":
        return list(questions)
    ids = list(raw)
    selected = [q_by_id[i] for i in ids if i in q_by_id]
    missing = [i for i in ids if i not in q_by_id]
    if missing:
        print(f"WARN: tier '{tier}' 引用了不存在的 Q-ID {missing}", file=sys.stderr)
    return selected


# ── Backend calls ──────────────────────────────────────────


def _login(base_url: str) -> str:
    resp = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"username": DEFAULT_USER, "password": DEFAULT_PASS},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _find_kb_id(base_url: str, token: str) -> str:
    resp = requests.get(
        f"{base_url}/api/v1/kb",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    kb_id = next(
        (kb["id"] for kb in resp.json() if DEFAULT_KB_HINT in kb.get("name", "")),
        None,
    )
    if not kb_id:
        sys.exit(
            f"ERROR: 找不到含 '{DEFAULT_KB_HINT}' 的知识库；用 RAGENT_KB_NAME_HINT 改匹配关键词"
        )
    return kb_id


def _retrieve(base_url: str, token: str, query: str, kb_id: str, top_k: int) -> dict:
    resp = requests.post(
        f"{base_url}/api/v1/retrieve",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "kb_ids": [kb_id], "top_k": top_k},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ── Output ─────────────────────────────────────────────────


def _print_summary(results: list[dict]) -> None:
    n = len(results)
    if not n:
        print("\n无结果")
        return
    have_gold = [r for r in results if r.get("gold_documents")]
    if not have_gold:
        print(f"\n=== 完成 {n} 题检索（无 gold 标注，仅打印 top1）===")
        for r in results:
            top1 = r.get("top1_doc") or "-"
            err = r.get("error")
            if err:
                print(f"  {r['id']}: ❌ {err}")
            else:
                print(
                    f"  {r['id']}: top1={top1}  hits={r['retrieved_count']}  {r['elapsed_ms']:.0f}ms"
                )
        return
    keys = list(have_gold[0]["metrics"].keys())
    agg = {k: sum(r["metrics"][k] for r in have_gold) / len(have_gold) for k in keys}
    print(f"\n=== {len(have_gold)}/{n} 题有 gold 标注，平均指标 ===")
    for k, v in agg.items():
        print(f"  {k}: {v:.3f}")


# ── Main ───────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", default="smoke", choices=["smoke", "regression", "full"])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None, help="在前 N 题上跑（调试用）")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()

    testset = _load_json(TESTSET_PATH)
    tiers_cfg = _load_json(TIERS_PATH)
    questions = _select_questions(testset, args.tier, tiers_cfg)
    if args.limit:
        questions = questions[: args.limit]
    print(f"Tier: {args.tier}  共 {len(questions)} 题  base_url: {args.base_url}")

    token = _login(args.base_url)
    kb_id = _find_kb_id(args.base_url, token)
    print(f"KB: {kb_id}\n")

    results: list[dict] = []
    for i, q in enumerate(questions, 1):
        t0 = time.monotonic()
        try:
            data = _retrieve(args.base_url, token, q["问题"], kb_id, args.top_k)
        except Exception as e:
            print(f"[{i}/{len(questions)}] {q['id']} ❌ retrieve error: {e}", flush=True)
            results.append(
                {
                    "id": q["id"],
                    "category": q.get("类别"),
                    "difficulty": q.get("难度"),
                    "question": q["问题"],
                    "error": str(e),
                }
            )
            continue
        elapsed_ms = (time.monotonic() - t0) * 1000
        items = data.get("items", [])
        retrieved_ids = [it["document_id"] for it in items]
        gold = q.get("gold_documents") or []
        result = {
            "id": q["id"],
            "category": q.get("类别"),
            "difficulty": q.get("难度"),
            "question": q["问题"],
            "retrieved_count": len(items),
            "top1_doc": items[0]["document_id"] if items else None,
            "retrieved_ids": retrieved_ids,
            "elapsed_ms": round(elapsed_ms, 1),
            "degraded": data.get("degraded", False),
            "gold_documents": gold,
        }
        if gold:
            result["metrics"] = compute_all(retrieved_ids, gold)
        results.append(result)
        icon = "✓" if not gold or result.get("metrics", {}).get("hit@10") else "·"
        m = result.get("metrics", {})
        print(
            f"[{i}/{len(questions)}] {q['id']} {icon} "
            f"top1={result['top1_doc']} hits={result['retrieved_count']} "
            f"{elapsed_ms:.0f}ms hit@10={m.get('hit@10', '-')}",
            flush=True,
        )

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果写入 {RESULT_PATH}")
    _print_summary(results)


if __name__ == "__main__":
    main()
