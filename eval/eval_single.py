"""逐个测试脚本 — 每题独立评测，带重试和延迟。

用法：
    D:/miniConda/envs/rag/python.exe eval_single.py [--limit N] [--offset N]
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "http://localhost:8000"
TESTSET_PATH = Path(__file__).parent / "sany_annual_reports" / "rag_testset.json"
RESULT_PATH = Path(__file__).parent / "sany_annual_reports" / "eval_results.json"

# ── Judge ─────────────────────────────────────────────────

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


def call_judge(question: str, reference: str, rag_answer: str) -> dict:
    """Call SiliconFlow DeepSeek-V3 as judge, with retries."""
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    prompt = JUDGE_PROMPT.format(
        question=question, reference=reference[:500], rag_answer=rag_answer[:1000]
    )
    for attempt in range(5):
        try:
            resp = requests.post(
                "https://api.siliconflow.cn/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-ai/DeepSeek-V3",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 200,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"] or ""
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if not content:
                time.sleep(3)
                continue
            score_match = re.search(r'"score"\s*:\s*(\d+)', content)
            if score_match:
                score = int(score_match.group(1))
                reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', content)
                reason = reason_match.group(1)[:60] if reason_match else ""
                return {"score": score, "reason": reason}
            time.sleep(2)
        except Exception as e:
            if attempt < 4:
                time.sleep(5)
            else:
                return {"score": -1, "reason": f"Judge error: {e}"}
    return {"score": -1, "reason": "Max retries exceeded"}


# ── RAG call ──────────────────────────────────────────────

def call_rag(query: str, token: str, kb_id: str) -> dict:
    """Call RAG API with retries."""
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{BASE_URL}/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"query": query, "knowledge_base_ids": [kb_id]},
                stream=True,
                timeout=120,
            )
            resp.raise_for_status()

            answer_parts = []
            sources_count = 0
            for line in resp.iter_lines(decode_unicode=True):
                if line is None:
                    continue
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    data = line[6:].strip()
                    if event_type == "token":
                        answer_parts.append(data)
                    elif event_type == "sources":
                        try:
                            sources_count = len(json.loads(data))
                        except:
                            pass
                    elif event_type == "done":
                        break

            answer = "".join(answer_parts).strip()
            if "AI 服务暂时不可用" in answer:
                if attempt < 2:
                    print("  ⚠️ Circuit breaker, waiting 30s...", flush=True)
                    time.sleep(30)
                    continue
                return {"answer": answer, "sources_count": sources_count, "error": "circuit_breaker"}
            return {"answer": answer, "sources_count": sources_count, "error": None}
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                return {"answer": "", "sources_count": 0, "error": str(e)}


# ── Main ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()

    # Login
    token = requests.post(f"{BASE_URL}/api/v1/auth/login", json={
        "username": "admin", "password": "admin123"
    }).json()["access_token"]

    # Find KB
    kbs = requests.get(f"{BASE_URL}/api/v1/kb", headers={"Authorization": f"Bearer {token}"}).json()
    kb_id = next((kb["id"] for kb in kbs if "三一重工" in kb["name"]), None)
    if not kb_id:
        print("ERROR: 找不到三一重工知识库")
        sys.exit(1)

    # Load test set
    with open(TESTSET_PATH, "r", encoding="utf-8") as f:
        testset = json.load(f)
    questions = testset["题目"][args.offset:]
    if args.limit:
        questions = questions[:args.limit]

    # Load existing results
    results = {}
    if RESULT_PATH.exists():
        with open(RESULT_PATH, "r", encoding="utf-8") as f:
            results = json.load(f)

    # Count already done
    done = sum(1 for q in questions if q["id"] in results and results[q["id"]].get("judge_score", -1) >= 0)
    print(f"共 {len(questions)} 题，已完成 {done} 题\n")

    for i, q in enumerate(questions):
        qid = q["id"]

        # Skip if already done
        if qid in results and results[qid].get("judge_score", -1) >= 0:
            continue

        print(f"[{i+1}/{len(questions)}] {qid} ({q['难度']}) {q['问题'][:40]}...", end=" ", flush=True)

        # Step 1: Call RAG
        rag = call_rag(q["问题"], token, kb_id)
        answer = rag["answer"]
        ans_len = len(answer)
        print(f"→ {ans_len}字", end=" ", flush=True)

        if not answer or rag.get("error"):
            print(f"❌ RAG failed: {rag.get('error', 'empty')}")
            results[qid] = {
                "question": q["问题"],
                "reference": q["参考答案"],
                "category": q["类别"],
                "difficulty": q["难度"],
                "rag_answer": "",
                "sources_count": 0,
                "error": rag.get("error", "empty"),
                "judge_score": -1,
                "judge_reason": f"RAG failed: {rag.get('error', 'empty')}",
            }
            with open(RESULT_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            time.sleep(5)
            continue

        # Step 2: Judge
        time.sleep(3)  # delay before judge
        judge = call_judge(q["问题"], q["参考答案"], answer)
        score = judge["score"]
        reason = judge["reason"]

        results[qid] = {
            "question": q["问题"],
            "reference": q["参考答案"],
            "category": q["类别"],
            "difficulty": q["难度"],
            "rag_answer": answer,
            "sources_count": rag["sources_count"],
            "error": None,
            "judge_score": score,
            "judge_reason": reason,
        }

        # Save after each question
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        score_icon = {3: "✅", 2: "🔵", 1: "🟡", 0: "❌"}.get(score, "⚪")
        print(f"{score_icon} {score}分: {reason[:40]}")

        # Delay before next question
        time.sleep(5)

    # Summary
    scored = {qid: r for qid, r in results.items() if r.get("judge_score", -1) >= 0}
    if scored:
        total = sum(r["judge_score"] for r in scored.values())
        max_score = len(scored) * 3
        print(f"\n=== 已完成 {len(scored)}/{len(testset['题目'])} 题 ===")
        print(f"总分: {total}/{max_score} ({total/max_score*100:.1f}%)")


if __name__ == "__main__":
    main()
