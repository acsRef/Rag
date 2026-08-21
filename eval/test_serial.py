"""逐个测试失败题（不并发），输出 RAG 真实答案便于诊断。"""
import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "http://localhost:8000"
TESTSET_PATH = "D:/PyProject/ragent-py/三一重工年报/rag_testset.json"

# C/H 失败的关键题
TARGETS = ["Q17", "Q18", "Q19", "Q20", "Q22",
           "Q50", "Q51", "Q52", "Q54", "Q31"]


def login():
    return requests.post(f"{BASE_URL}/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"}).json()["access_token"]


def get_kb_id(token):
    for kb in requests.get(f"{BASE_URL}/api/v1/kb",
        headers={"Authorization": f"Bearer {token}"}).json():
        if "三一重工" in kb["name"]:
            return kb["id"]
    raise RuntimeError("KB not found")


def call_rag(token, kb_id, query, timeout=120):
    body = {"query": query, "knowledge_base_ids": [kb_id]}
    resp = requests.post(f"{BASE_URL}/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body, stream=True, timeout=timeout)
    answer = ""
    event = None
    sources = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            if event == "token":
                answer += line[6:]
            elif event == "sources":
                try:
                    sources = json.loads(line[6:])
                except Exception:
                    pass
            event = None
    return {"answer": answer, "sources": sources}


def main():
    token = login()
    kb_id = get_kb_id(token)

    with open(TESTSET_PATH, encoding="utf-8") as f:
        testset = json.load(f)
    questions = {q["id"]: q for q in testset["题目"]}

    for qid in TARGETS:
        q = questions[qid]
        print(f"\n{'='*70}")
        print(f"[{qid}] {q['类别']} | {q['难度']}")
        print(f"Q: {q['问题']}")
        print(f"参考: {q['参考答案'][:120]}...")

        try:
            t0 = time.time()
            rag = call_rag(token, kb_id, q["问题"])
            elapsed = time.time() - t0

            print(f"\nRAG ({elapsed:.1f}s):")
            print(f"  Answer: {rag['answer'][:500]}")
            print(f"\nSources ({len(rag['sources'])}):")
            for i, s in enumerate(rag['sources'][:5]):
                fn = s.get("filename", "")[:25]
                sec = s.get("section_path", "")[:50]
                print(f"  [{i+1}] {fn} | {sec}")
        except Exception as e:
            print(f"  ERROR: {e}")

        time.sleep(3)  # 每题间隔 3 秒


if __name__ == "__main__":
    main()
