"""详细测试脚本 — 记录完整的检索和回答过程。

输出：
  - eval_detail_report.md：详细报告（每题含 sources、section_path、answer、judge）
  - 控制台实时进度

用法：
    D:/miniConda/envs/rag/python.exe eval_detail.py [--limit N] [--category C]
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "http://localhost:8000"
TESTSET_PATH = Path(__file__).parent / "sany_annual_reports" / "rag_testset.json"
REPORT_PATH = Path(__file__).parent / "sany_annual_reports" / "eval_detail_report.md"

# ── Judge ─────────────────────────────────────────────────

JUDGE_PROMPT = """你是一个严格的RAG评测裁判。

## 题目信息
- 问题：{question}
- 类别：{category}
- 参考答案：{reference}
- 答案依据：{source}
- 考察的易错点：{pitfall}

## RAG系统的回答
{rag_answer}

## 评分规则 (0-3分)
- 3分：核心信息全部覆盖，关键数字/单位正确
- 2分：包含部分正确信息，但有关键细节遗漏或小幅偏差
- 1分：涉及相关内容但存在明显错误（年份搞混、数字张冠李戴）
- 0分：完全错误、拒答可回答的问题、或编造信息

注意：
1. 数字要精确匹配，允许合理四舍五入
2. 单位错误算明显错误
3. 年份/来源混淆算明显错误

仅输出JSON: {{"score": N, "reason": "60字以内理由"}}"""


def call_judge(q_data: dict, rag_answer: str) -> dict:
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    prompt = JUDGE_PROMPT.format(
        question=q_data["问题"],
        category=q_data["类别"],
        reference=q_data["参考答案"][:500],
        source=q_data["答案依据"][:200],
        pitfall=q_data["考察的RAG易错点"][:200],
        rag_answer=rag_answer[:1200],
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
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            score_match = re.search(r'"score"\s*:\s*(\d+)', content)
            if score_match:
                score = int(score_match.group(1))
                reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', content)
                reason = reason_match.group(1)[:80] if reason_match else ""
                return {"score": score, "reason": reason}
            time.sleep(2)
        except Exception as e:
            if attempt < 4:
                time.sleep(5)
            else:
                return {"score": -1, "reason": str(e)[:80]}
    return {"score": -1, "reason": "max retries"}


# ── RAG call with full detail ─────────────────────────────


def call_rag_detail(query: str, token: str, kb_id: str) -> dict:
    """Call RAG API and capture full detail (answer, sources, thinking, etc.)."""
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
            thinking_parts = []
            sources = []
            event_type = None

            for line in resp.iter_lines(decode_unicode=True):
                if line is None:
                    continue
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    data = line[6:].strip()
                    if event_type == "token":
                        answer_parts.append(data)
                    elif event_type == "thinking":
                        thinking_parts.append(data)
                    elif event_type == "sources":
                        try:
                            sources = json.loads(data)
                        except:
                            pass
                    elif event_type == "done":
                        break
                    elif event_type == "error":
                        try:
                            err = json.loads(data)
                            return {
                                "answer": "",
                                "sources": [],
                                "thinking": "",
                                "error": err.get("error", data),
                            }
                        except Exception:
                            return {"answer": "", "sources": [], "thinking": "", "error": data}

            answer = "".join(answer_parts).strip()
            thinking = "".join(thinking_parts).strip()

            if "AI 服务暂时不可用" in answer:
                if attempt < 2:
                    time.sleep(30)
                    continue
                return {"answer": answer, "sources": [], "thinking": "", "error": "circuit_breaker"}

            return {"answer": answer, "sources": sources, "thinking": thinking, "error": None}
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                return {"answer": "", "sources": [], "thinking": "", "error": str(e)}


# ── Report generation ─────────────────────────────────────


def generate_report(records: list, path: Path):
    lines = [
        "# RAG 详细评测报告",
        f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"\n题目数: {len(records)}",
    ]

    # Summary
    scored = [r for r in records if r["score"] >= 0]
    if scored:
        total = sum(r["score"] for r in scored)
        max_s = len(scored) * 3
        lines.extend(
            [
                "\n## 总体结果",
                f"- 已评分: {len(scored)}/{len(records)}",
                f"- 总分: {total}/{max_s} ({total / max_s * 100:.1f}%)",
                f"- 完全正确(3分): {sum(1 for r in scored if r['score'] == 3)}",
                f"- 基本正确(2分): {sum(1 for r in scored if r['score'] == 2)}",
                f"- 部分正确(1分): {sum(1 for r in scored if r['score'] == 1)}",
                f"- 完全错误(0分): {sum(1 for r in scored if r['score'] == 0)}",
            ]
        )

    # By category
    from collections import defaultdict

    cats = defaultdict(lambda: {"total": 0, "count": 0})
    for r in scored:
        cat = r["category"][:1]
        cats[cat]["total"] += r["score"]
        cats[cat]["count"] += 1

    if cats:
        lines.append("\n## 分类统计")
        lines.append("\n| 类别 | 题数 | 总分 | 平均分 |")
        lines.append("|------|------|------|--------|")
        for cat in sorted(cats.keys()):
            c = cats[cat]
            avg = c["total"] / c["count"]
            lines.append(f"| {cat}类 | {c['count']} | {c['total']}/{c['count'] * 3} | {avg:.2f} |")

    # Detailed per-question
    lines.append("\n## 详细记录\n")

    for i, r in enumerate(records):
        score_icon = {3: "✅", 2: "🔵", 1: "🟡", 0: "❌"}.get(r["score"], "⚪")
        lines.append(f"---\n### {i + 1}. {r['qid']} {score_icon} {r['score']}分\n")
        lines.append(f"**问题**: {r['question']}\n")
        lines.append(f"**类别**: {r['category']} | **难度**: {r['difficulty']}\n")

        if r["error"]:
            lines.append(f"\n⚠️ **错误**: {r['error']}\n")
            continue

        # Sources
        if r["sources"]:
            lines.append(f"\n**检索来源** ({len(r['sources'])} 条):")
            for j, s in enumerate(r["sources"]):
                fn = s.get("filename", "")[:30]
                sec = s.get("section_path", "")[:50]
                score = s.get("score", 0)
                lines.append(f"  {j + 1}. `{fn}` → `{sec}` (score={score:.3f})")

        # Reference answer
        lines.append("\n**参考答案**:")
        lines.append(f"> {r['reference'][:300]}\n")

        # RAG answer
        lines.append("\n**RAG 回答**:")
        lines.append(f"> {r['answer'][:600]}\n")

        # Thinking (if any)
        if r.get("thinking"):
            lines.append(f"\n**思考过程** ({len(r['thinking'])}字):")
            lines.append("```")
            lines.append(r["thinking"][:300])
            lines.append("```\n")

        # Judge
        lines.append(f"\n**评分**: {r['score']}分 — {r['reason']}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已保存: {path}")


# ── Main ──────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="只测前N题")
    parser.add_argument("--category", type=str, default=None, help="只测某类 (如 C)")
    args = parser.parse_args()

    # Login
    token = requests.post(
        f"{BASE_URL}/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]

    # Find KB
    kbs = requests.get(f"{BASE_URL}/api/v1/kb", headers={"Authorization": f"Bearer {token}"}).json()
    kb_id = next((kb["id"] for kb in kbs if "三一重工" in kb["name"]), None)
    if not kb_id:
        print("ERROR: 找不到三一重工知识库")
        sys.exit(1)

    # Load test set
    with open(TESTSET_PATH, encoding="utf-8") as f:
        testset = json.load(f)
    questions = testset["题目"]

    # Filter
    if args.category:
        questions = [q for q in questions if q["类别"].startswith(args.category)]
        print(f"筛选 {args.category} 类题目: {len(questions)} 题")
    if args.limit:
        questions = questions[: args.limit]

    print(f"共 {len(questions)} 题，逐个测试...\n")

    records = []
    for i, q in enumerate(questions):
        qid = q["id"]
        print(
            f"[{i + 1}/{len(questions)}] {qid} ({q['难度']}) {q['问题'][:40]}...",
            end=" ",
            flush=True,
        )

        # RAG call
        rag = call_rag_detail(q["问题"], token, kb_id)
        answer = rag["answer"]
        sources = rag["sources"]
        thinking = rag.get("thinking", "")

        if rag["error"]:
            print(f"❌ RAG错误: {rag['error']}")
            records.append(
                {
                    "qid": qid,
                    "question": q["问题"],
                    "category": q["类别"],
                    "difficulty": q["难度"],
                    "reference": q["参考答案"],
                    "answer": "",
                    "sources": [],
                    "thinking": "",
                    "score": -1,
                    "reason": f"RAG错误: {rag['error']}",
                    "error": rag["error"],
                }
            )
            time.sleep(3)
            continue

        print(f"→ {len(answer)}字, {len(sources)}sources", end=" ", flush=True)

        # Judge
        time.sleep(3)
        judge = call_judge(q, answer)
        score = judge["score"]
        reason = judge["reason"]

        score_icon = {3: "✅", 2: "🔵", 1: "🟡", 0: "❌"}.get(score, "⚪")
        print(f"{score_icon} {score}分")

        records.append(
            {
                "qid": qid,
                "question": q["问题"],
                "category": q["类别"],
                "difficulty": q["难度"],
                "reference": q["参考答案"],
                "answer": answer,
                "sources": sources,
                "thinking": thinking,
                "score": score,
                "reason": reason,
                "error": None,
            }
        )

        # Save intermediate
        generate_report(records, REPORT_PATH)

        time.sleep(5)

    # Final summary
    scored = [r for r in records if r["score"] >= 0]
    if scored:
        total = sum(r["score"] for r in scored)
        max_s = len(scored) * 3
        print(f"\n{'=' * 50}")
        print(f"总计: {len(scored)}/{len(records)} 题已评分")
        print(f"总分: {total}/{max_s} ({total / max_s * 100:.1f}%)")

    generate_report(records, REPORT_PATH)


if __name__ == "__main__":
    main()
