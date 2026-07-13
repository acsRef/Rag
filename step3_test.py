"""Step 3: 端到端对比测试 — 20 条 query × 配置 1 (_needs_decomposition 已上线)"""
import json
import time
import requests
import sys

KB_ID = "a18e62187f234e7d"
BASE = "http://localhost:8000"
LOG = "step3_results.md"

QUERIES = [
    # A类: 单文档纵深 (5条)
    ("A1", "M3 网关的工作温度范围", "单文档-产品规格"),
    ("A2", "Q4 研发投入金额", "单文档-Q4财报"),
    ("A3", "员工年假天数", "单文档-员工手册"),
    ("A4", "安全审计发现了什么问题", "单文档-安全审计"),
    ("A5", "M3 对比 M4 的主要差异", "单文档-对比分析"),
    # B类: 跨文档对比 (10条)
    ("B1", "M3 和 M4 的处理器性能差异", "跨文档-规格书+对比"),
    ("B2", "Q4 和 Q1 的营收对比", "跨文档-Q4+Q1财报"),
    ("B3", "安全审计和 FAQ 对 M3 安全特性的描述", "跨文档-审计+FAQ"),
    ("B4", "2025 年销售策略和实际业绩对比", "跨文档-策略+财报"),
    ("B5", "员工福利政策和培训内容对比", "跨文档-手册+培训"),
    ("B6", "产品路线图和 M3 规格书对未来功能规划", "跨文档-路线图+规格书"),
    ("B7", "客户案例和 FAQ 中 M3 的部署方式", "跨文档-案例+FAQ"),
    ("B8", "供应商评估和财报对成本的分析", "跨文档-供应商+财报"),
    ("B9", "M3 产品规格和销售卖点对比", "跨文档-规格书+策略"),
    ("B10", "不同文档对 M3 协议支持的描述差异", "跨文档-多文档对比"),
    # C类: 跨文档综合 (5条)
    ("C1", "总结公司 2025 年整体经营情况", "综合-多维"),
    ("C2", "M3 产品相关的所有技术参数和性能指标", "综合-产品"),
    ("C3", "2025 年销售策略和实际收入对比分析", "综合-策略+业绩"),
    ("C4", "M3 产品的售后和技术支持体系", "综合-售后"),
    ("C5", "公司的人员管理和培训体系", "综合-人力"),
]


def get_token():
    r = requests.post(f"{BASE}/api/v1/auth/login",
                      json={"username": "admin", "password": "admin123"})
    return r.json()["access_token"]


def query_sse(token: str, query: str) -> dict:
    resp = {
        "query": query,
        "sources": [],
        "answer": "",
        "error": None,
        "duration_ms": 0,
    }
    t0 = time.monotonic()
    current_event = None
    try:
        r = requests.post(
            f"{BASE}/api/v1/chat/stream",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
            json={
                "query": query,
                "knowledge_base_ids": [KB_ID],
                "conversation_id": None,
            },
            stream=True,
            timeout=120,
        )
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]
                if current_event == "sources":
                    try:
                        resp["sources"] = json.loads(data_str)
                    except json.JSONDecodeError:
                        pass
                elif current_event == "error":
                    try:
                        err = json.loads(data_str)
                        resp["error"] = err.get("error", data_str)
                    except json.JSONDecodeError:
                        resp["error"] = data_str
                elif current_event == "token":
                    resp["answer"] += data_str
                elif current_event == "thinking" and not resp["answer"]:
                    resp["answer"] += data_str
    except Exception as e:
        resp["error"] = str(e)
    resp["duration_ms"] = int((time.monotonic() - t0) * 1000)
    return resp


def eval_answer(qid: str, cat: str, query: str, result: dict) -> dict:
    """Evaluate answer quality scoring."""
    ans = result.get("answer", "")
    srcs = result.get("sources", [])
    has_error = result.get("error") is not None
    src_count = len(srcs) if isinstance(srcs, list) else 0
    ans_len = len(ans)

    # Completeness heuristic: 1-5 based on answer length and source count
    score = 1
    if ans_len > 50:
        score = 2
    if ans_len > 200 and src_count >= 1:
        score = 3
    if ans_len > 500 and src_count >= 2:
        score = 4
    if ans_len > 1000 and src_count >= 3:
        score = 5
    if has_error:
        score = 1

    return {
        "qid": qid,
        "cat": cat,
        "query": query,
        "score": score,
        "src_count": src_count,
        "ans_len": ans_len,
        "has_error": has_error,
        "duration_ms": result["duration_ms"],
        "answer_preview": ans[:300],
        "sources_preview": [s.get("title", s.get("chunk_id", "?"))[:60] for s in (srcs if isinstance(srcs, list) else [])],
    }


def log_results(results: list[dict]):
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("# Step 3: 端到端测试结果\n\n")
        f.write(f"配置: 配置 1 (_needs_decomposition 已上线) + 新 METADATA_PROMPT\n")
        f.write(f"KB: {KB_ID}\n")
        f.write(f"运行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        # Summary table
        f.write("## 汇总\n\n")
        f.write("| ID | 类别 | Query | 分数 | 来源数 | 答案长度 | 耗时(ms) | 错误 |\n")
        f.write("|----|------|-------|------|--------|----------|----------|------|\n")
        for r in results:
            f.write(f"| {r['qid']} | {r['cat']} | {r['query'][:30]} | {r['score']} | {r['src_count']} | {r['ans_len']} | {r['duration_ms']} | {'❌' if r['has_error'] else '✅'} |\n")

        # Score breakdown
        cats = {}
        for r in results:
            cats.setdefault(r['cat'], []).append(r['score'])
        f.write("\n## 按类别平均分\n\n")
        f.write("| 类别 | 平均分 | 数量 |\n")
        f.write("|------|--------|------|\n")
        for cat, scores in sorted(cats.items()):
            avg = sum(scores) / len(scores)
            f.write(f"| {cat} | {avg:.1f} | {len(scores)} |\n")

        # Detail per query
        f.write("\n## 详情\n\n")
        for r in results:
            f.write(f"### {r['qid']}: {r['query']}\n\n")
            f.write(f"- **类别**: {r['cat']}\n")
            f.write(f"- **评分**: {r['score']}/5\n")
            f.write(f"- **来源数**: {r['src_count']}\n")
            f.write(f"- **答案长度**: {r['ans_len']} 字\n")
            f.write(f"- **耗时**: {r['duration_ms']}ms\n")
            f.write(f"- **错误**: {'❌ ' + r.get('error', '') if r['has_error'] else '✅ 无'}\n\n")
            if r['sources_preview']:
                f.write("**来源chunks**:\n")
                for s in r['sources_preview']:
                    f.write(f"  - {s}\n")
                f.write("\n")
            f.write("**回答预览**:\n\n")
            f.write(f"> {r['answer_preview']}\n\n")
            f.write("---\n\n")


def main():
    token = get_token()
    print(f"Token obtained: {token[:20]}...")
    print(f"KB ID: {KB_ID}")
    print(f"Starting {len(QUERIES)} queries...\n")

    results = []
    for qid, query, cat in QUERIES:
        sys.stdout.write(f"  [{qid}] {query[:40]:40s} ... ")
        sys.stdout.flush()
        result = query_sse(token, query)
        eval_result = eval_answer(qid, cat, query, result)
        results.append(eval_result)
        print(f"score={eval_result['score']} srcs={eval_result['src_count']} len={eval_result['ans_len']} t={eval_result['duration_ms']}ms")

    log_results(results)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Results logged to: {LOG}")
    print()
    cats = {}
    for r in results:
        cats.setdefault(r['cat'], []).append(r['score'])
    for cat, scores in sorted(cats.items()):
        avg = sum(scores) / len(scores)
        print(f"  {cat:20s}: avg={avg:.1f} ({len(scores)} queries)")
    totals = [r['score'] for r in results]
    print(f"  {'TOTAL':20s}: avg={sum(totals)/len(totals):.1f} ({len(totals)} queries)")
    print(f"  Avg duration: {sum(r['duration_ms'] for r in results)/len(results):.0f}ms")
    print(f"  Error rate: {sum(1 for r in results if r['has_error'])}/{len(results)}")


if __name__ == "__main__":
    main()
