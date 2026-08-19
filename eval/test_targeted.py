"""针对性测试脚本：只测关键 bad case，快速迭代。

设计：
- 测试集：C/H 类失败的关键题（~10 题）
- 每个问题单独跑，单独评分（避免批量 API 限流）
- 输出对比报告，便于看每次改动效果
"""
import json
import os
import re
import time
from pathlib import Path
from dotenv import load_dotenv

import requests

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "http://localhost:8000"
TESTSET_PATH = "D:/PyProject/ragent-py/三一重工年报/rag_testset.json"

# C/H 类失败的关键题（来自之前的 bad case 分析）
TARGET_QUESTIONS = [
    # C-跨文档对比：检索只拿到单年数据
    "Q17",  # 2023-2025年营收
    "Q18",  # 2023-2025年归母净利润
    "Q19",  # 近三年海外收入
    "Q20",  # 2023-2025年挖掘机械收入
    "Q22",  # 2023-2025年员工总数
    # H-错误前提纠偏：模型顺着错误前提回答
    "Q50",  # 混凝土机械收入同比增长
    "Q51",  # 研发投入连续三年加大
    "Q52",  # 分红力度缩水
    "Q54",  # 海外国家数量比2023年增加
    # 多步推理
    "Q31",  # 判断盈利改善是否靠国内市场需求爆发
]


def login():
    resp = requests.post(f"{BASE_URL}/api/v1/auth/login", json={
        "username": "admin", "password": "admin123",
    })
    return resp.json()["access_token"]


def get_kb_id(token):
    resp = requests.get(f"{BASE_URL}/api/v1/kb", headers={"Authorization": f"Bearer {token}"})
    for kb in resp.json():
        if "三一重工" in kb["name"]:
            return kb["id"]
    raise RuntimeError("未找到三一重工年报 KB")


def call_rag(token, kb_id, query, conversation_id=None):
    body = {"query": query, "knowledge_base_ids": [kb_id]}
    if conversation_id:
        body["conversation_id"] = conversation_id
    resp = requests.post(
        f"{BASE_URL}/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body, stream=True, timeout=180,
    )
    answer = ""
    event_type = None
    sources = []
    conv_id = conversation_id
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: "):
            data = line[6:].strip()
            if event_type == "metadata":
                try:
                    conv_id = json.loads(data).get("conversation_id", conv_id)
                except json.JSONDecodeError:
                    pass
            elif event_type == "token":
                answer += data
            elif event_type == "sources":
                try:
                    sources = json.loads(data)
                except json.JSONDecodeError:
                    pass
            event_type = None
    return {"answer": answer, "sources": sources, "conv_id": conv_id}


def judge(question_data, rag_answer):
    # 紧凑 prompt，避免长 prompt 触发超时
    prompt = f"""根据参考答案判断RAG回答的准确度(0-3分)。

问题: {question_data['问题']}
参考答案: {question_data['参考答案'][:300]}
RAG回答: {rag_answer[:800]}

3=完全正确;2=基本正确有小偏差;1=部分正确有明显错误;0=错误/拒答/编造。
仅输出JSON: {{"score":N,"reason":"<60字理由"}}"""
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    base_url = os.environ.get("JUDGE_BASE_URL", "https://api.siliconflow.cn/v1")
    model = os.environ.get("JUDGE_MODEL", "deepseek-ai/DeepSeek-V3")
    # 多次重试
    for attempt in range(2):
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.1, "max_tokens": 200},
                timeout=60,
            )
            content = resp.json()["choices"][0]["message"]["content"] or ""
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if not content:
                if attempt < 1:
                    time.sleep(2)
                    continue
                return -1, "Empty"
            score_match = re.search(r'"score"\s*:\s*(\d+)', content)
            if score_match:
                score = int(score_match.group(1))
                reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', content)
                reason = reason_match.group(1)[:60] if reason_match else ""
                return score, reason
            if attempt < 1:
                time.sleep(2)
                continue
            return -1, f"Parse fail: {content[:50]}"
        except requests.exceptions.RequestException as e:
            if attempt < 1:
                time.sleep(3)
                continue
            return -1, f"Req err: {str(e)[:40]}"


def run(limit=None):
    with open(TESTSET_PATH, "r", encoding="utf-8") as f:
        testset = json.load(f)
    questions = {q["id"]: q for q in testset["题目"]}
    targets = TARGET_QUESTIONS[:limit] if limit else TARGET_QUESTIONS

    token = login()
    kb_id = get_kb_id(token)

    results = []
    for qid in targets:
        q = questions[qid]
        print(f"\n[{qid}] {q['类别']} | {q['难度']}")
        print(f"  Q: {q['问题']}")

        try:
            rag = call_rag(token, kb_id, q["问题"])
            answer = rag["answer"]
            print(f"  A: {answer[:200]}...")

            # 评分（多次取稳定）
            scores = []
            for _ in range(2):
                score, reason = judge(q, answer)
                if score >= 0:
                    scores.append((score, reason))
                time.sleep(1)
            if scores:
                from collections import Counter
                final_score = Counter(s for s, _ in scores).most_common(1)[0][0]
                final_reason = scores[0][1]
            else:
                final_score = -1
                final_reason = "all judge failed"

            print(f"  Score: {final_score} - {final_reason[:80]}")
            results.append({"qid": qid, "score": final_score, "reason": final_reason,
                          "answer": answer, "sources_count": len(rag["sources"])})
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"qid": qid, "score": -1, "reason": str(e), "answer": "", "sources_count": 0})

        time.sleep(2)

    # 汇总
    scored = [r for r in results if r["score"] >= 0]
    print(f"\n{'='*60}")
    print(f"汇总 ({len(scored)}/{len(results)} 评分成功)")
    print(f"{'='*60}")
    if scored:
        total = sum(r["score"] for r in scored)
        print(f"总分: {total}/{len(scored)*3} ({total/len(scored)*3/3*100:.1f}%)")
        print(f"平均: {total/len(scored):.2f}/3")
    for r in results:
        score_str = str(r["score"]) if r["score"] >= 0 else "未评"
        print(f"  {r['qid']}: {score_str} - {r['reason'][:60]}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="只测前N题")
    args = parser.parse_args()
    run(limit=args.limit)
