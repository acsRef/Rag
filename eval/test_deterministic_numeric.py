"""Smoke test deterministic_numeric against real calibration cases.

关键验证目标:
- Q55: 86.4亿元 → direct_hit (PDF 有 "86.4 亿元")
- Q26: 0.80元 → derived (3.1 + 4.9 元/10股 换算) 或 not_found（已知局限）
- Q33: 8,408,057千元 → direct_hit (但语义错 — numeric check 的边界!)
"""

import json
import sys

sys.path.insert(0, ".")
from eval.deterministic_numeric import summarize, verify_number_claims

# 加载 PDF evidence
pdf = {
    2025: open("eval/sany_annual_reports/三一重工_2025年年度报告.pymupdf4llm.md", encoding="utf-8").read(),
    2024: open("eval/sany_annual_reports/三一重工_2024年年度报告.pymupdf4llm.md", encoding="utf-8").read(),
}

# Q26/Q33/Q55 的 gold_evidence 从 testset 取
ds = json.load(open("eval/sany_annual_reports/rag_testset.json", encoding="utf-8"))
by_id = {q["id"]: q for q in ds["题目"]}


def run_case(qid, extra_evidence=""):
    cal = json.load(open(f"eval/judge_calibration_v3/{qid}.json", encoding="utf-8"))
    answer = cal["generation_answer"]
    ev_snippets = [e["snippet"] for e in by_id[qid].get("gold_evidence", [])]
    if extra_evidence:
        ev_snippets.append(extra_evidence)

    verdicts = verify_number_claims(answer, ev_snippets)
    s = summarize(verdicts)
    print(f"=== {qid} ===")
    print(f"  {s}")
    for v in verdicts:
        marker = {"direct_hit": "✓", "derived": "≈", "not_found": "✗"}[v.status]
        print(
            f"  {marker} {v.value:,.4g} [{v.unit}] {v.status}"
            + (f" ({', '.join(v.derived_from)})" if v.derived_from else "")
        )
    print()
    return verdicts


# Case 1: Q55 — RAG 说 86.4亿+115%，evidence 是 PDF snippet
run_case("Q55")

# Case 2: Q26 — RAG 说 0.80 = 0.31 + 0.49; evidence 只有年度预案 snippet
# 补充中期分红原文片段测试 derived 层
q26_extra = (
    "注：2025 年半年度，公司以总股本 8,431,402,624 股为基数，向全体股东每 10 股派现金红利 3.1 元(含税)，"
    "共计派发现金红利 2,613,734,813.44 元，中期现金红利已于 2025 年 10 月 15 日发放。 "
    "每10股派息数（元）（含税）||4.9"
)
run_case("Q26", extra_evidence=q26_extra)

# Case 3: Q33 — RAG 说 8,408,057 是"调整后值"（语义错）
# numeric check 会 direct_hit（数字存在），暴露 numeric-only check 的盲区
q33_evidence_2025 = (
    "|主要会计数据|2025年|202|4年|本期比上"
    " |归属于上市公司股东的净利润|8,408,057|5,955,567|5,975,451|41.18|4,527,451|"
)
cal33 = json.load(open("eval/judge_calibration_v3/Q33.json", encoding="utf-8"))
verdicts = verify_number_claims(cal33["generation_answer"], [q33_evidence_2025])
print("=== Q33 (semantic-error boundary case) ===")
print(f"  {summarize(verdicts)}")
print("  ⚠️ 预期: 数字全部 direct_hit 但答案语义错误")
print("  → numeric check 只能证明 '数字有出处'，不能证明 '归属正确'")
print("  → 这就是为什么它是 evaluator 的一层，不是全部")

# Case 4: 反例 — 净利润 vs 总资产 同数字不同 metric
fake_answer = "公司净利润为1733亿元。"
fake_evidence = ["截至报告期末，公司总资产1,733 亿元，归属于上市公司股东的净资产883.3 亿元。"]
verdicts = verify_number_claims(fake_answer, fake_evidence)
print("=== Counter-example (1733 净利润 vs 总资产) ===")
print(f"  {summarize(verdicts)}")
print("  ⚠️ numeric check 无法区分 metric 归属 — 需要 entity/metric 层 (Phase B 后续)")
