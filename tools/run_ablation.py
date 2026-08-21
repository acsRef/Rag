"""8 组 retrieval ablation 自动化脚本（Day 2 晚上）。

策略：依次启动 backend（不同 env vars），跑 regression 20 题，采集指标，停 backend。
仅 retrieval 层指标（不调 LLM judge）；generation eval 留给后续单独跑。

8 组配置：
  1. Baseline          — 所有检索策略关 + gate 关（当前生产）
  2. All Retrieval ON  — 5 个策略全开
  3. + Cross-doc       — 只开 cross_doc
  4. + Section Boost   — 只开 section_boost
  5. + Section Supp.   — 只开 section_supplement
  6. + Year Supp.      — 只开 year_supplement
  7. + Evidence Gate   — gate=true min_coverage=0.7
  8. + Evidence Gate .5 — gate=true min_coverage=0.5

每个配置启动 backend → 跑 regression 20 题 → 记录 hit/recall/MRR/latency → 停 backend。

用法：
    D:/miniConda/envs/rag/python.exe tools/run_ablation.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
EVAL_PY = ROOT / "eval" / "retrieval_eval.py"
RESULT_PATH = ROOT / "eval" / "sany_annual_reports" / "retrieval_results.json"
OUTPUT_PATH = ROOT / "docs" / "plans" / "2026-08-23-ablation-data.json"

# 8 组配置（顺序即 ablation 表的行序）
CONFIGS = [
    {
        "name": "Baseline",
        "desc": "所有检索策略关 + evidence_gate 关（生产默认）",
        "env": {
            "CROSS_DOC_ENABLED": "false",
            "SECTION_BOOST_ENABLED": "false",
            "SECTION_SUPPLEMENT_ENABLED": "false",
            "YEAR_SUPPLEMENT_ENABLED": "false",
            "QUERY_DECOMPOSITION_ENABLED": "false",
            "EVIDENCE_GATE_ENABLED": "false",
        },
    },
    {
        "name": "All Retrieval ON",
        "desc": "5 个检索策略全开（cross_doc/section_boost/section_supplement/year_supplement/query_decomposition）",
        "env": {
            "CROSS_DOC_ENABLED": "true",
            "SECTION_BOOST_ENABLED": "true",
            "SECTION_SUPPLEMENT_ENABLED": "true",
            "YEAR_SUPPLEMENT_ENABLED": "true",
            "QUERY_DECOMPOSITION_ENABLED": "true",
            "EVIDENCE_GATE_ENABLED": "false",
        },
    },
    {
        "name": "+ Cross-doc",
        "desc": "只开 cross_doc_enabled",
        "env": {
            "CROSS_DOC_ENABLED": "true",
            "EVIDENCE_GATE_ENABLED": "false",
        },
    },
    {
        "name": "+ Section Boost",
        "desc": "只开 section_boost_enabled",
        "env": {
            "SECTION_BOOST_ENABLED": "true",
            "EVIDENCE_GATE_ENABLED": "false",
        },
    },
    {
        "name": "+ Section Supp.",
        "desc": "只开 section_supplement_enabled",
        "env": {
            "SECTION_SUPPLEMENT_ENABLED": "true",
            "EVIDENCE_GATE_ENABLED": "false",
        },
    },
    {
        "name": "+ Year Supp.",
        "desc": "只开 year_supplement_enabled",
        "env": {
            "YEAR_SUPPLEMENT_ENABLED": "true",
            "EVIDENCE_GATE_ENABLED": "false",
        },
    },
    {
        "name": "+ Evidence Gate (.7)",
        "desc": "evidence_gate_enabled=true, min_coverage=0.7",
        "env": {
            "EVIDENCE_GATE_ENABLED": "true",
            "EVIDENCE_MIN_COVERAGE": "0.7",
        },
    },
    {
        "name": "+ Evidence Gate (.5)",
        "desc": "evidence_gate_enabled=true, min_coverage=0.5",
        "env": {
            "EVIDENCE_GATE_ENABLED": "true",
            "EVIDENCE_MIN_COVERAGE": "0.5",
        },
    },
]


def _wait_backend(port: int = 8000, timeout: int = 20) -> bool:
    for i in range(timeout):
        try:
            r = requests.get(f"http://localhost:{port}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _run_eval_regression(tier: str = "regression", top_k: int = 10) -> dict:
    """跑一次 retrieval_eval，读取 results.json 聚合指标。"""
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, str(EVAL_PY), "--tier", tier, "--top-k", str(top_k)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        print(f"  eval error: {proc.stderr[-500:]}")
        return {}
    # 读取 results.json
    if not RESULT_PATH.exists():
        return {}
    with open(RESULT_PATH, encoding="utf-8") as f:
        results = json.load(f)
    # 聚合
    have_gold = [r for r in results if r.get("gold_documents")]
    if not have_gold:
        return {"n": 0, "results": results}
    keys = list(have_gold[0]["metrics"].keys())
    agg = {k: sum(r["metrics"][k] for r in have_gold) / len(have_gold) for k in keys}
    avg_latency = sum(r.get("elapsed_ms", 0) for r in results) / len(results) if results else 0
    return {
        "n_total": len(results),
        "n_with_gold": len(have_gold),
        "agg": agg,
        "avg_latency_ms": round(avg_latency, 1),
        "results": results,
    }


def _kill_backend():
    """停止可能残留的 backend 进程。"""
    if sys.platform == "win32":
        # taskkill /F /IM python.exe 风险大（会杀其他 python）；用 tasklist 找进程
        subprocess.run(["taskkill", "/F", "/FI", "WINDOWTITLE eq ragent*"],
                       capture_output=True)
        # 兜底：find PID by netstat 8000 + kill
        try:
            r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if ":8000" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                    print(f"  killed PID {pid}")
        except Exception:
            pass
    else:
        subprocess.run(["pkill", "-f", "app.main"], capture_output=True)


def main():
    aggregated = []
    for cfg in CONFIGS:
        print(f"\n=== {cfg['name']}: {cfg['desc']} ===")
        print(f"  env: {cfg['env']}")

        # 1. 停旧 backend
        _kill_backend()
        time.sleep(2)

        # 2. 启动新 backend
        env = os.environ.copy()
        env.update({k: str(v) for k, v in cfg["env"].items()})
        env["LOG_LEVEL"] = "INFO"
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.main"],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        print(f"  started PID {proc.pid}")
        if not _wait_backend():
            print(f"  ✗ backend failed to start within 20s")
            proc.kill()
            continue

        # 3. 跑 regression
        t0 = time.monotonic()
        result = _run_eval_regression("regression", 10)
        elapsed = time.monotonic() - t0
        print(f"  eval elapsed {elapsed:.1f}s")
        if result:
            print(f"  hit@5={result['agg'].get('hit@5', 0):.3f}  "
                  f"hit@10={result['agg'].get('hit@10', 0):.3f}  "
                  f"recall@5={result['agg'].get('recall@5', 0):.3f}  "
                  f"recall@10={result['agg'].get('recall@10', 0):.3f}  "
                  f"MRR={result['agg'].get('mrr', 0):.3f}  "
                  f"avg_latency={result['avg_latency_ms']:.0f}ms")
            aggregated.append({
                "name": cfg["name"],
                "desc": cfg["desc"],
                "env": cfg["env"],
                "metrics": result["agg"],
                "n_total": result["n_total"],
                "n_with_gold": result["n_with_gold"],
                "avg_latency_ms": result["avg_latency_ms"],
                "eval_elapsed_s": round(elapsed, 1),
            })

        # 4. 停 backend（下一轮重启前）
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        time.sleep(2)

    # 汇总写文件
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(aggregated, f, ensure_ascii=False, indent=2)
    print(f"\n汇总写入 {OUTPUT_PATH}")
    print("\n=== 汇总表 ===")
    print(f"{'Config':<24}{'hit@5':<10}{'hit@10':<10}{'recall@5':<11}{'recall@10':<11}{'MRR':<10}{'latency':<10}")
    for a in aggregated:
        m = a["metrics"]
        print(f"{a['name']:<24}{m.get('hit@5', 0):<10.3f}{m.get('hit@10', 0):<10.3f}"
              f"{m.get('recall@5', 0):<11.3f}{m.get('recall@10', 0):<11.3f}"
              f"{m.get('mrr', 0):<10.3f}{a['avg_latency_ms']:<10.0f}")


if __name__ == "__main__":
    main()
