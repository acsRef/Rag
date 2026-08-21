"""手动测试 C/H 关键问题，逐个跑避免 judge API 限流。"""
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# 加载 .env
load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "http://localhost:8000"
TESTSET_PATH = "D:/PyProject/ragent-py/三一重工年报/rag_testset.json"

# 登录
token = requests.post(f"{BASE_URL}/api/v1/auth/login", json={
    "username": "admin", "password": "admin123"
}).json()["access_token"]

# 找 KB
kb_id = requests.get(f"{BASE_URL}/api/v1/kb", headers={"Authorization": f"Bearer {token}"}).json()
kb_id = [k["id"] for k in kb_id if "三一重工" in k["name"]][0]

# 加载测试集
with open(TESTSET_PATH, encoding="utf-8") as f:
    testset = json.load(f)
questions = {q["id"]: q for q in testset["题目"]}

# 测试目标题
TARGETS = ["Q17", "Q18", "Q19", "Q20", "Q22",  # C-跨文档对比
           "Q50", "Q51", "Q52", "Q54"]  # H-错误前提

# 调 RAG API
def call_rag(query):
    resp = requests.post(f"{BASE_URL}/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "knowledge_base_ids": [kb_id]},
        stream=True, timeout=180)

    answer = ""
    event_type = None
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: ") and event_type == "token":
            answer += line[6:]
            event_type = None
        elif line.startswith("data: ") and event_type:
            event_type = None
    return answer

# 评分（用 MiniMax M3）
def judge(question_data, rag_answer):
    prompt = f"""判断RAG系统回答是否正确。

问题：{question_data['问题']}
参考答案：{question_data['参考答案']}
考察易错点：{question_data['考察的RAG易错点']}

RAG回答：
{rag_answer[:1500]}

按 0-3 分打分：
- 3：完全正确，核心信息全部覆盖
- 2：基本正确，有小遗漏或偏差
- 1：部分正确，有明显错误
- 0：错误/拒答/编造

请仅输出 JSON：{{"score": N, "reason": "理由"}}"""

    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    base_url = os.environ.get("JUDGE_BASE_URL", "https://api.siliconflow.cn/v1")
    model = os.environ.get("JUDGE_MODEL", "deepseek-ai/DeepSeek-V3")

    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.1, "max_tokens": 300},
        timeout=60,
    )
    content = resp.json()["choices"][0]["message"]["content"] or ""
    # 提取 JSON
    import re
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    match = re.search(r'\{[^}]+\}', content)
    if match:
        try:
            r = json.loads(match.group())
            return int(r["score"]), r.get("reason", "")
        except:
            pass
    return -1, f"Parse failed: {content[:80]}"


print(f"\n{'='*60}\nC/H 类逐题测试\n{'='*60}\n")

results = {}
for qid in TARGETS:
    q = questions[qid]
    print(f"\n[{qid}] {q['类别']} | {q['难度']}")
    print(f"Q: {q['问题']}")

    try:
        answer = call_rag(q["问题"])
        print(f"A: {answer[:200]}...")

        # 多次评分取稳定的
        scores = []
        for attempt in range(2):
            score, reason = judge(q, answer)
            if score >= 0:
                scores.append((score, reason))
            time.sleep(1)

        if scores:
            # 取多数分
            from collections import Counter
            final_score = Counter(s for s, _ in scores).most_common(1)[0][0]
            final_reason = scores[0][1]
            print(f"Score: {final_score} - {final_reason[:100]}")
            results[qid] = {"score": final_score, "reason": final_reason, "answer_len": len(answer)}
        else:
            results[qid] = {"score": -1, "reason": "all judge failed", "answer_len": len(answer)}
    except Exception as e:
        print(f"ERROR: {e}")
        results[qid] = {"score": -1, "reason": str(e)}

    time.sleep(2)

# 汇总
print(f"\n{'='*60}\n汇总\n{'='*60}\n")
for cat in ["C", "H"]:
    print(f"\n{cat} 类:")
    cat_scores = [r for qid, r in results.items() if qid in TARGETS and q["id"] if cat in questions[qid]["类别"]] if False else []
    # simpler:
    cat_qids = [qid for qid in TARGETS if cat in questions[qid]["类别"][:1]]
    for qid in cat_qids:
        if qid in results:
            print(f"  {qid}: {results[qid]['score']} - {results[qid]['reason'][:60]}")
