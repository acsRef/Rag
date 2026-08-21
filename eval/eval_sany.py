"""三一重工年报 RAG 评测脚本

读取 rag_testset.json，对每道题调用 RAG API 获取回答，
然后用 MiniMax LLM 做裁判打分，输出评测报告。

用法：
    D:/miniConda/envs/rag/python.exe eval_sany.py [--limit N] [--skip-judge] [--resume]
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

BASE_URL = "http://localhost:8000"
TESTSET_PATH = Path(__file__).parent / "sany_annual_reports" / "rag_testset.json"
RESULT_PATH = Path(__file__).parent / "sany_annual_reports" / "eval_results.json"
REPORT_PATH = Path(__file__).parent / "sany_annual_reports" / "eval_report.md"


# ── Auth ──────────────────────────────────────────────────


def login() -> str:
    resp = requests.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={
            "username": "admin",
            "password": "admin123",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── SSE stream parser ─────────────────────────────────────


def call_rag(query: str, token: str, kb_id: str, conversation_id: str | None = None) -> dict:
    """Call the chat/stream endpoint and collect the full response.

    Returns dict with: answer, thinking, sources, conversation_id, error.
    """
    body = {
        "query": query,
        "knowledge_base_ids": [kb_id],
    }
    if conversation_id:
        body["conversation_id"] = conversation_id

    resp = requests.post(
        f"{BASE_URL}/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()

    answer_parts = []
    thinking_parts = []
    sources = []
    conv_id = conversation_id
    error = None
    event_type = None

    for line in resp.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: "):
            data = line[6:].strip()
            if event_type == "metadata":
                try:
                    m = json.loads(data)
                    conv_id = m.get("conversation_id", conv_id)
                except json.JSONDecodeError:
                    pass
            elif event_type == "sources":
                try:
                    sources = json.loads(data)
                except json.JSONDecodeError:
                    pass
            elif event_type == "thinking":
                thinking_parts.append(data)
            elif event_type == "token":
                answer_parts.append(data)
            elif event_type == "error":
                try:
                    e = json.loads(data)
                    error = e.get("error", data)
                except json.JSONDecodeError:
                    error = data
            elif event_type == "done":
                break
            elif event_type is None and data.startswith("{"):
                # Fallback metadata
                try:
                    m = json.loads(data)
                    if "conversation_id" in m:
                        conv_id = m["conversation_id"]
                except json.JSONDecodeError:
                    pass
            event_type = None

    return {
        "answer": "".join(answer_parts),
        "thinking": "".join(thinking_parts),
        "sources": sources,
        "conversation_id": conv_id,
        "error": error,
    }


# ── LLM Judge ─────────────────────────────────────────────

JUDGE_PROMPT = """你是一个严格的RAG系统评测裁判。你的任务是判断RAG系统的回答是否正确。

## 题目信息
- 问题：{question}
- 类别：{category}
- 难度：{difficulty}
- 参考答案：{reference}
- 答案依据：{source}
- 考察的易错点：{pitfall}
- 常见错误答案：{common_errors}

## RAG系统的回答
{rag_answer}

## 评分规则
请根据以下标准打分（0-3分）：
- **3分（完全正确）**：回答包含参考答案的核心信息，关键数字/事实正确，单位正确，没有明显错误
- **2分（基本正确）**：回答包含部分正确信息，但有关键细节遗漏或小幅偏差（如单位换算略有误差、缺少某个限定条件）
- **1分（部分正确）**：回答涉及了相关内容但存在明显错误（如年份搞混、数字张冠李戴、单位错误）
- **0分（完全错误）**：回答完全错误、未回答、拒答了可回答的问题、或编造了不存在的信息

特别注意：
1. 对于拒答题（参考答案说"无法回答"），如果RAG系统也正确表示无法回答，得3分
2. 对于纠偏题（问题前提错误），如果RAG系统指出了前提错误，得3分
3. 数字要精确匹配，允许合理的四舍五入（如"约1733亿"≈"1733.0亿"）
4. 单位错误（千元 vs 亿元）算明显错误
5. 年份/来源混淆算明显错误

## 输出格式
请严格按以下JSON格式输出，不要有其他内容：
{{"score": 0-3, "reason": "简短理由（50字以内）"}}"""


def judge_answer(
    question_data: dict, rag_answer: str, api_key: str, base_url: str, model: str
) -> dict:
    """Use LLM to judge the RAG answer."""
    prompt = JUDGE_PROMPT.format(
        question=question_data["问题"],
        category=question_data["类别"],
        difficulty=question_data["难度"],
        reference=question_data["参考答案"],
        source=question_data["答案依据"],
        pitfall=question_data["考察的RAG易错点"],
        common_errors=question_data["常见错误答案"],
        rag_answer=rag_answer,
    )

    for attempt in range(3):
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 300,
                },
                timeout=90,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"] or ""

            # Strip <think>...</think> tags (MiniMax reasoning model outputs them)
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

            if not content:
                if attempt < 2:
                    time.sleep(2)
                    continue
                return {"score": -1, "reason": "Empty response after 3 attempts"}

            # Parse JSON from response - handle both {"score":N,"reason":"..."} and partial
            # Extract score number directly (most robust)
            score_match = re.search(r'"score"\s*:\s*(\d+)', content)
            if score_match:
                score = int(score_match.group(1))
                reason_match = re.search(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
                reason = reason_match.group(1) if reason_match else ""
                reason = reason.replace('\\"', '"').replace("\\n", " ")
                return {"score": score, "reason": reason[:80]}

            # Fallback: try parsing any JSON object
            match = re.search(r"\{[^}]+\}", content)
            if match:
                try:
                    result = json.loads(match.group())
                    return {"score": int(result["score"]), "reason": result.get("reason", "")}
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass

            if attempt < 2:
                time.sleep(2)
                continue
            return {"score": -1, "reason": f"Parse failed: {content[:100]}"}
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                time.sleep(3)
                continue
            return {"score": -1, "reason": f"Request error: {e}"}


# ── Main evaluation loop ──────────────────────────────────


def run_eval(limit: int | None = None, skip_judge: bool = False, resume: bool = True):
    token = login()

    # Find the KB
    resp = requests.get(f"{BASE_URL}/api/v1/kb", headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    kbs = resp.json()
    kb_id = None
    for kb in kbs:
        if "三一重工" in kb["name"]:
            kb_id = kb["id"]
            break
    if not kb_id:
        print("ERROR: 找不到三一重工年报知识库，请先上传文档")
        sys.exit(1)
    print(f"使用知识库: {kb_id}")

    # Load test set
    with open(TESTSET_PATH, encoding="utf-8") as f:
        testset = json.load(f)
    questions = testset["题目"]
    if limit:
        questions = questions[:limit]
    print(f"共 {len(questions)} 道题")

    # Load existing results for resume
    results = {}
    if resume and RESULT_PATH.exists():
        with open(RESULT_PATH, encoding="utf-8") as f:
            results = json.load(f)
        done = sum(1 for r in results.values() if r.get("rag_answer"))
        print(f"已有 {done}/{len(questions)} 道题结果，继续...")

    # Run each question
    for i, q in enumerate(questions):
        qid = q["id"]
        if qid in results and results[qid].get("rag_answer"):
            print(f"[{i + 1}/{len(questions)}] {qid} 已有结果，跳过")
            continue

        print(
            f"[{i + 1}/{len(questions)}] {qid} ({q['难度']}) {q['问题'][:40]}...",
            end=" ",
            flush=True,
        )

        try:
            rag_result = call_rag(q["问题"], token, kb_id)
            answer = rag_result["answer"].strip()
            print(f"→ {len(answer)}字", end=" ", flush=True)

            results[qid] = {
                "question": q["问题"],
                "reference": q["参考答案"],
                "category": q["类别"],
                "difficulty": q["难度"],
                "rag_answer": answer,
                "sources_count": len(rag_result.get("sources", [])),
                "error": rag_result.get("error"),
                "judge_score": None,
                "judge_reason": None,
            }

            # Save intermediate results
            with open(RESULT_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

            print("✓")
        except Exception as e:
            print(f"ERROR: {e}")
            results[qid] = {
                "question": q["问题"],
                "reference": q["参考答案"],
                "category": q["类别"],
                "difficulty": q["难度"],
                "rag_answer": "",
                "error": str(e),
                "judge_score": None,
                "judge_reason": None,
            }

        # Rate limit between questions — 3s to avoid SiliconFlow rate limit
        time.sleep(3)

    # Judge phase
    if not skip_judge:
        print("\n=== 评分阶段 ===")
        # Read env for judge LLM — default to SiliconFlow DeepSeek-V3
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).parent / ".env")
        api_key = os.environ.get("SILICONFLOW_API_KEY", "") or os.environ.get("MINIMAX_API_KEY", "")
        base_url = os.environ.get(
            "JUDGE_BASE_URL",
            os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
        )
        model = os.environ.get(
            "JUDGE_MODEL", os.environ.get("CHAT_MODEL", "deepseek-ai/DeepSeek-V3")
        )

        if not api_key:
            print("WARNING: SILICONFLOW_API_KEY not set, skipping judge phase")
        else:
            for i, q in enumerate(questions):
                qid = q["id"]
                if qid not in results or not results[qid].get("rag_answer"):
                    continue
                if results[qid].get("judge_score") is not None and results[qid]["judge_score"] >= 0:
                    print(f"[{i + 1}/{len(questions)}] {qid} 已评分，跳过")
                    continue

                print(f"[{i + 1}/{len(questions)}] 评分 {qid}...", end=" ", flush=True)
                try:
                    judge = judge_answer(q, results[qid]["rag_answer"], api_key, base_url, model)
                    results[qid]["judge_score"] = judge["score"]
                    results[qid]["judge_reason"] = judge["reason"]
                    print(f"→ {judge['score']}分: {judge['reason']}")
                except Exception as e:
                    print(f"ERROR: {e}")
                    results[qid]["judge_score"] = -1
                    results[qid]["judge_reason"] = str(e)

                time.sleep(2)  # judge rate limit

            with open(RESULT_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

    # Generate report
    generate_report(results, questions)
    print(f"\n结果保存至: {RESULT_PATH}")
    print(f"报告保存至: {REPORT_PATH}")


def generate_report(results: dict, questions: list):
    """Generate a markdown report."""
    lines = [
        "# 三一重工年报 RAG 评测报告",
        f"\n评测时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"\n题目总数: {len(questions)}",
    ]

    scored = {
        qid: r
        for qid, r in results.items()
        if r.get("judge_score") is not None and r["judge_score"] >= 0
    }
    unscored = {
        qid: r for qid, r in results.items() if r.get("judge_score") is None or r["judge_score"] < 0
    }

    if scored:
        total_score = sum(r["judge_score"] for r in scored.values())
        max_score = len(scored) * 3
        avg_score = total_score / len(scored)
        perfect = sum(1 for r in scored.values() if r["judge_score"] == 3)
        partial = sum(1 for r in scored.values() if r["judge_score"] == 2)
        wrong = sum(1 for r in scored.values() if r["judge_score"] <= 1)

        lines.extend(
            [
                "\n## 总体结果",
                f"- 已评分: {len(scored)}/{len(questions)}",
                f"- 总分: {total_score}/{max_score} ({total_score / max_score * 100:.1f}%)",
                f"- 平均分: {avg_score:.2f}/3",
                f"- 完全正确(3分): {perfect} ({perfect / len(scored) * 100:.1f}%)",
                f"- 基本正确(2分): {partial} ({partial / len(scored) * 100:.1f}%)",
                f"- 部分/完全错误(0-1分): {wrong} ({wrong / len(scored) * 100:.1f}%)",
            ]
        )

    # By category
    categories = {}
    for qid, r in scored.items():
        cat = r.get("category", "未知")
        if cat not in categories:
            categories[cat] = {"total": 0, "score": 0, "count": 0}
        categories[cat]["total"] += r["judge_score"]
        categories[cat]["count"] += 1

    if categories:
        lines.append("\n## 分类统计")
        lines.append("\n| 类别 | 题数 | 总分 | 平均分 | 满分率 |")
        lines.append("|------|------|------|--------|--------|")
        for cat in sorted(categories.keys()):
            c = categories[cat]
            avg = c["total"] / c["count"]
            perfect_count = sum(
                1 for qid, r in scored.items() if r.get("category") == cat and r["judge_score"] == 3
            )
            lines.append(
                f"| {cat} | {c['count']} | {c['total']}/{c['count'] * 3} | {avg:.2f} | {perfect_count / c['count'] * 100:.0f}% |"
            )

    # By difficulty
    difficulties = {}
    for qid, r in scored.items():
        diff = r.get("difficulty", "未知")
        if diff not in difficulties:
            difficulties[diff] = {"total": 0, "score": 0, "count": 0}
        difficulties[diff]["total"] += r["judge_score"]
        difficulties[diff]["count"] += 1

    if difficulties:
        lines.append("\n## 难度统计")
        lines.append("\n| 难度 | 题数 | 总分 | 平均分 |")
        lines.append("|------|------|------|--------|")
        for diff in ["简单", "中等", "困难"]:
            if diff in difficulties:
                d = difficulties[diff]
                avg = d["total"] / d["count"]
                lines.append(
                    f"| {diff} | {d['count']} | {d['total']}/{d['count'] * 3} | {avg:.2f} |"
                )

    # Detail table
    lines.append("\n## 详细结果")
    lines.append("\n| 题号 | 类别 | 难度 | 问题(摘要) | 得分 | 理由 |")
    lines.append("|------|------|------|-----------|------|------|")
    for q in questions:
        qid = q["id"]
        r = results.get(qid, {})
        score = r.get("judge_score")
        score_str = str(score) if score is not None and score >= 0 else "未评"
        reason = (r.get("judge_reason") or "")[:40]
        q_summary = q["问题"][:25] + "..." if len(q["问题"]) > 25 else q["问题"]
        lines.append(
            f"| {qid} | {q['类别'][:8]} | {q['难度']} | {q_summary} | {score_str} | {reason} |"
        )

    # Failed / error cases
    errors = {qid: r for qid, r in results.items() if r.get("error")}
    if errors:
        lines.append(f"\n## 错误/异常 ({len(errors)}题)")
        for qid, r in errors.items():
            lines.append(f"- **{qid}**: {r['error']}")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="三一重工年报 RAG 评测")
    parser.add_argument("--limit", type=int, default=None, help="只评测前N题")
    parser.add_argument("--skip-judge", action="store_true", help="跳过LLM评分阶段")
    parser.add_argument("--resume", action="store_true", default=True, help="继续之前的评测")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="重新开始评测")
    args = parser.parse_args()

    run_eval(limit=args.limit, skip_judge=args.skip_judge, resume=args.resume)
