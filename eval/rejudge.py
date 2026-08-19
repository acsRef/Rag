"""逐个重评 eval_results.json 中评分为 -1 的题。
不重跑 RAG（答案已缓存），只重新调裁判 API 多次。
"""
import json
import os
import re
import time
from pathlib import Path
from dotenv import load_dotenv

import requests

load_dotenv(Path(__file__).parent / ".env")

RESULTS_PATH = "D:/PyProject/ragent-py/eval/sany_annual_reports/eval_results.json"
TESTSET_PATH = "D:/PyProject/ragent-py/eval/sany_annual_reports/rag_testset.json"


def judge(question_data, rag_answer):
    prompt = f"""根据参考答案判断RAG回答的准确度(0-3分)。

问题: {question_data['问题']}
参考答案: {question_data['参考答案'][:300]}
RAG回答: {rag_answer[:800]}

3=完全正确;2=基本正确有小偏差;1=部分正确有明显错误;0=错误/拒答/编造。
仅输出JSON: {{"score":N,"reason":"<60字理由"}}"""
    # 慢没关系，关键要准：超时 180s，重试 5 次
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    base_url = os.environ.get("JUDGE_BASE_URL", "https://api.siliconflow.cn/v1")
    model = os.environ.get("JUDGE_MODEL", "deepseek-ai/DeepSeek-V3")

    for attempt in range(5):
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.1, "max_tokens": 200},
                timeout=180,  # 给大模型充足时间
            )
            content = resp.json()["choices"][0]["message"]["content"] or ""
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if not content:
                if attempt < 4:
                    time.sleep(5)  # 退避
                    continue
                return -1, "Empty"
            score_match = re.search(r'"score"\s*:\s*(\d+)', content)
            if score_match:
                score = int(score_match.group(1))
                reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', content)
                reason = reason_match.group(1)[:60] if reason_match else ""
                return score, reason
            if attempt < 4:
                time.sleep(5)
                continue
            return -1, f"Parse: {content[:50]}"
        except requests.exceptions.RequestException as e:
            if attempt < 4:
                time.sleep(5)
                continue
            return -1, f"Req: {str(e)[:40]}"


def main():
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    with open(TESTSET_PATH, "r", encoding="utf-8") as f:
        testset = json.load(f)
    questions = {q["id"]: q for q in testset["题目"]}

    # 找出未评分的题
    targets = [qid for qid, r in results.items()
               if r.get("judge_score") is None or r.get("judge_score") < 0]
    print(f"需要重评 {len(targets)} 题: {targets}\n")

    updated = 0
    for i, qid in enumerate(targets):
        r = results[qid]
        if not r.get("rag_answer"):
            print(f"[{i+1}/{len(targets)}] {qid} 无 RAG 答案，跳过")
            continue

        q = questions.get(qid, {})
        score, reason = judge(q, r["rag_answer"])

        if score >= 0:
            results[qid]["judge_score"] = score
            results[qid]["judge_reason"] = reason
            updated += 1
            print(f"[{i+1}/{len(targets)}] {qid} → {score}分 ({reason[:50]})")
        else:
            print(f"[{i+1}/{len(targets)}] {qid} → 仍失败 ({reason})")

        time.sleep(2)

    # 保存更新
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n更新 {updated}/{len(targets)} 题")


if __name__ == "__main__":
    main()
