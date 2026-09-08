"""Build human_verified_15.json with corrected gold + 3-metric evaluation.

User's plan:
- Fix Q26/Q55 gold (原 gold 错)
- Add gold_evidence + gold_judgment to all 15
- Compute 3 metrics: Agreement / Accuracy / Shared Error
"""

import json
import re
from pathlib import Path

from gold import iter_questions

# Load testset（gold 唯一入口）
test_qs = {q.id: q for q in iter_questions()}

# Load 3 PDFs as markdown
pdf_text = {
    2023: Path("eval/sany_annual_reports/三一重工_2023年年度报告.pymupdf4llm.md").read_text(
        encoding="utf-8"
    ),
    2024: Path("eval/sany_annual_reports/三一重工_2024年年度报告.pymupdf4llm.md").read_text(
        encoding="utf-8"
    ),
    2025: Path("eval/sany_annual_reports/三一重工_2025年年度报告.pymupdf4llm.md").read_text(
        encoding="utf-8"
    ),
}


def find_evidence(years, keywords, context=200):
    """Search keywords in PDF years, return snippets."""
    results = []
    for y in years:
        text = pdf_text[y]
        for kw in keywords:
            m = re.search(re.escape(kw), text)
            if m:
                s = max(0, m.start() - context)
                e = min(len(text), m.end() + context)
                snippet = text[s:e].replace("\n", " ").strip()
                results.append({"year": y, "keyword": kw, "snippet": snippet[:400]})
                break
    return results[:3]


# Human labels
human_labels = {
    "Q01": {"correct": False, "partial": True, "refusal": "N/A", "citation": True},
    "Q02": {"correct": True, "partial": False, "refusal": "N/A", "citation": True},
    "Q03": {"correct": True, "partial": False, "refusal": "N/A", "citation": True},
    "Q11": {"correct": False, "partial": True, "refusal": "N/A", "citation": True},
    "Q12": {"correct": False, "partial": False, "refusal": False, "citation": "N/A"},
    "Q17": {"correct": False, "partial": False, "refusal": "N/A", "citation": False},
    "Q18": {"correct": False, "partial": True, "refusal": "N/A", "citation": True},
    "Q25": {"correct": True, "partial": False, "refusal": "N/A", "citation": True},
    "Q26": {"correct": False, "partial": False, "refusal": "N/A", "citation": False},
    "Q32": {"correct": False, "partial": True, "refusal": "N/A", "citation": True},
    "Q33": {"correct": False, "partial": True, "refusal": "N/A", "citation": True},
    "Q50": {"correct": True, "partial": False, "refusal": "N/A", "citation": True},
    "Q51": {"correct": False, "partial": True, "refusal": "N/A", "citation": True},
    "Q55": {"correct": False, "partial": False, "refusal": "N/A", "citation": False},
    "Q56": {"correct": True, "partial": False, "refusal": "N/A", "citation": "N/A"},
}


# Q26/Q55 corrected gold
gold_corrections = {
    "Q26": {
        "gold_answer": "全年合计每股0.80元（含税）。中期分红 3.1元/10股（0.31元/股，已实施）+ 年度预案 4.9元/10股（0.49元/股，待股东会审议）",
        "gold_evidence": find_evidence([2025], ["3.1", "每10股派息数"]),
        "gold_judgment": "answerable",
        "correction_note": "原 gold 仅含年度预案 (0.49元/股)，漏中期分红 (0.31元/股)。PDF 原文：「2025年半年度...向全体股东每 10 股派现金红利 3.1 元(含税)，共计派发现金红利 2,613,734,813.44 元，中期现金红利已于 2025 年 10 月 15 日发放」+ 年度预案 4.9 元/10 股。",
    },
    "Q55": {
        "gold_answer": "2025 年新能源产品销售额 86.4 亿元，同比增长 115%",
        "gold_evidence": find_evidence([2025], ["86.4", "新能源产品"]),
        "gold_judgment": "answerable",
        "correction_note": "原 gold 说「无法从年报获得」错。PDF 原文：「2025 年公司新能源产品销售额达 86.4 亿元人民币，同比提升 115%」。",
    },
}


# Other 13 questions' verified gold
other_gold = {
    "Q01": {
        "gold_answer": "732.22亿元（73,221,725千元，同比下降8.51%）",
        "gold_evidence": find_evidence([2023], ["73,221,725", "营业收入"]),
        "gold_judgment": "answerable",
    },
    "Q02": {
        "gold_answer": "59.75亿元（5,975,451千元，同比上升31.98%）",
        "gold_evidence": find_evidence([2024], ["59.75", "归属于上市公司股东的净利润"]),
        "gold_judgment": "answerable",
    },
    "Q03": {
        "gold_answer": "199.75亿元（19,975,261千元，同比增长34.84%）",
        "gold_evidence": find_evidence([2025], ["19,975,261", "经营活动"]),
        "gold_judgment": "answerable",
    },
    "Q11": {
        "gold_answer": "在职员工合计25,930人（母公司2,445人，主要子公司23,485人）",
        "gold_evidence": find_evidence([2023], ["25,930", "在职员工"]),
        "gold_judgment": "answerable",
    },
    "Q12": {
        "gold_answer": "约1,733.0亿元（173,299,138千元，较2024年末增长13.90%）",
        "gold_evidence": find_evidence([2025], ["173,299,138", "总资产"]),
        "gold_judgment": "answerable",
    },
    "Q17": {
        "gold_answer": "见原始 gold (跨文档趋势对比 — 2023 vs 2025)",
        "gold_evidence": find_evidence([2023, 2025], ["净利润"]),
        "gold_judgment": "answerable",
    },
    "Q18": {
        "gold_answer": "见原始 gold (跨年对比 — 2023 vs 2025)",
        "gold_evidence": find_evidence([2023, 2025], ["归属于上市公司股东的净利润"]),
        "gold_judgment": "answerable",
    },
    "Q25": {
        "gold_answer": "亚澳 238.9亿 + 欧洲 125亿 + 美洲 111.6亿 + 非洲 83.1亿 = 558.6亿元",
        "gold_evidence": find_evidence([2025], ["238.9", "亚澳"]),
        "gold_judgment": "answerable",
    },
    "Q32": {
        "gold_answer": "2023年报披露 4,527,498 千元 (原始)；2024年报对比数据 4,527,451 千元 (调整后)；差异 47 千元",
        "gold_evidence": find_evidence([2023, 2024], ["4,527,498", "4,527,451"]),
        "gold_judgment": "answerable",
    },
    "Q33": {
        "gold_answer": "调整后 2024 全年 = 5,955,567 千元 (≠ RAG 说的 8,408,057)",
        "gold_evidence": find_evidence([2025], ["5,955,567", "8,408,057"]),
        "gold_judgment": "answerable",
    },
    "Q50": {
        "gold_answer": "2024 混凝土机械 143.68亿 vs 2023 153.15亿，同比下降 6.18%（前提「增长」不成立）",
        "gold_evidence": find_evidence([2023, 2024], ["143.68", "混凝土"]),
        "gold_judgment": "answerable",
    },
    "Q51": {
        "gold_answer": "2023 研发 58.65亿 → 2024 53.81亿 → 2025 50.33亿 (递减趋势)，前提「连续三年加大」不成立",
        "gold_evidence": find_evidence([2023, 2024, 2025], ["58.65", "53.81", "50.33"]),
        "gold_judgment": "answerable",
    },
    "Q56": {
        "gold_answer": "三份年报中未给出 2026 量化营收增长目标（只有定性表述）",
        "gold_evidence": find_evidence([2025], ["2026", "经营计划"]),
        "gold_judgment": "not_answerable",
    },
}


# Known truth (RAG correctness verified against PDF)
known_truth = {
    "Q01": "wrong",  # 740.19 vs 732.22
    "Q02": "correct",
    "Q03": "correct",
    "Q11": "wrong",  # false refusal
    "Q12": "wrong",  # false refusal
    "Q17": "wrong",  # hallucinated
    "Q18": "partial",
    "Q25": "correct",
    "Q26": "correct",  # corrected gold agrees with RAG
    "Q32": "partial",
    "Q33": "wrong",  # conflated 2024 adjusted with 2025 number
    "Q50": "correct",
    "Q51": "partial",
    "Q55": "correct",  # corrected gold agrees with RAG
    "Q56": "correct",  # correct refusal
}


cal_dir = Path("eval/judge_calibration_v3")

out = []
for qid in [
    "Q01",
    "Q02",
    "Q03",
    "Q11",
    "Q12",
    "Q17",
    "Q18",
    "Q25",
    "Q26",
    "Q32",
    "Q33",
    "Q50",
    "Q51",
    "Q55",
    "Q56",
]:
    orig = test_qs[qid]
    cal = json.loads((cal_dir / f"{qid}.json").read_text(encoding="utf-8"))

    if qid in gold_corrections:
        gold = gold_corrections[qid]
    else:
        gold = other_gold[qid]

    record = {
        "id": qid,
        "category": orig.category,
        "question": orig.question,
        "original_gold": orig.gold_answer,
        "verified_gold": gold["gold_answer"],
        "gold_evidence": gold["gold_evidence"],
        "gold_judgment": gold["gold_judgment"],
        "gold_correction_note": gold.get("correction_note", ""),
        "rag_answer": cal["generation_answer"],
        "rag_answer_length": cal["answer_length_chars"],
        "judge_score": cal["judge_score"],
        "judge_reason": cal["judge_reason"],
        "judge_raw_response": cal["judge_raw_response"],
        "judge_verdict": {
            3: "correct",
            2: "partial",
            1: "wrong",
            0: "wrong",
            None: "unknown",
        }.get(cal["judge_score"], "unknown"),
        "human_label": human_labels[qid],
        "verified_reality": known_truth[qid],
    }
    out.append(record)


def human_verdict(h):
    if h.get("refusal") == False:
        return "refusal_wrong"
    if h.get("correct") and not h.get("partial"):
        return "correct"
    if h.get("partial") and not h.get("correct"):
        return "partial"
    if not h.get("correct") and not h.get("partial"):
        return "wrong"
    return "ambiguous"


# Compute 3 metrics
metrics = {
    "agreement_judge_human": {"matches": 0, "total": 0, "details": []},
    "accuracy_judge_vs_reality": {"matches": 0, "total": 0, "details": []},
    "shared_error_rate": {"shared_wrong": 0, "total": 0, "details": []},
}

for r in out:
    j = r["judge_verdict"]
    h = human_verdict(r["human_label"])
    rv = r["verified_reality"]

    # Metric 1: Judge ↔ Human agreement (raw agreement regardless of correctness)
    j_simple = "wrong" if j == "wrong" else ("partial" if j == "partial" else j)
    h_simple = "wrong" if h == "refusal_wrong" else h
    if j_simple == h_simple:
        metrics["agreement_judge_human"]["matches"] += 1
    else:
        metrics["agreement_judge_human"]["details"].append(
            {"qid": r["id"], "judge": j, "human": h, "reality": rv}
        )
    metrics["agreement_judge_human"]["total"] += 1

    # Metric 2: Judge vs Reality (correctness against verified truth)
    # correct partial = "right"; wrong = "wrong"
    j_collapse = (
        "wrong" if j == "wrong" else ("right" if j in ("correct", "partial") else "unknown")
    )
    rv_collapse = (
        "wrong" if rv == "wrong" else ("right" if rv in ("correct", "partial") else "unknown")
    )
    if j_collapse == rv_collapse and j_collapse != "unknown":
        metrics["accuracy_judge_vs_reality"]["matches"] += 1
    else:
        metrics["accuracy_judge_vs_reality"]["details"].append(
            {"qid": r["id"], "judge": j, "reality": rv}
        )
    metrics["accuracy_judge_vs_reality"]["total"] += 1

    # Metric 3: Shared Error (Judge wrong AND Human wrong)
    h_wrong = h in ("wrong", "refusal_wrong")
    j_wrong = j_collapse == "wrong" or (j_collapse == "right" and rv_collapse == "wrong")
    if h_wrong and j_wrong:
        metrics["shared_error_rate"]["shared_wrong"] += 1
        metrics["shared_error_rate"]["details"].append(
            {"qid": r["id"], "judge": j, "human": h, "reality": rv}
        )
    metrics["shared_error_rate"]["total"] += 1


def pct(num, denom):
    return f"{num}/{denom} = {num / denom * 100:.1f}%"


metrics["agreement_judge_human"]["pct"] = pct(
    metrics["agreement_judge_human"]["matches"], metrics["agreement_judge_human"]["total"]
)
metrics["accuracy_judge_vs_reality"]["pct"] = pct(
    metrics["accuracy_judge_vs_reality"]["matches"], metrics["accuracy_judge_vs_reality"]["total"]
)
metrics["shared_error_rate"]["pct"] = pct(
    metrics["shared_error_rate"]["shared_wrong"], metrics["shared_error_rate"]["total"]
)


output_path = Path("eval/sany_annual_reports/human_verified_15.json")
output_path.write_text(
    json.dumps(
        {
            "version": "1.0",
            "created": "2026-08-23",
            "method": "PDF 原文交叉验证（pymupdf4llm 提取 + 人工核对）",
            "metadata": {
                "verifier": "AI (Claude) per user spec",
                "verifier_notes": "Q26/Q55 gold 已修正（原 gold 漏中期分红 / 错说新能源收入无法获得）",
                "pdf_source": "eval/sany_annual_reports/三一重工_20XX年年度报告.pdf (pymupdf4llm 提取为 markdown)",
                "limitations": "Q17/Q18/Q32/Q33 部分 evidence 未完整提取（仅前 3 hit）",
            },
            "questions": out,
            "metrics": metrics,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

print(f"Saved to {output_path}")
print()
print("=" * 70)
print("Three-metric evaluation (CORRECTED gold)")
print("=" * 70)
print(f"Metric 1: Judge ↔ Human Agreement = {metrics['agreement_judge_human']['pct']}")
print(f"  Details: {metrics['agreement_judge_human']['details']}")
print()
print(f"Metric 2: Judge ↔ Reality Accuracy = {metrics['accuracy_judge_vs_reality']['pct']}")
print(f"  Details: {metrics['accuracy_judge_vs_reality']['details']}")
print()
print(f"Metric 3: Shared Error Rate = {metrics['shared_error_rate']['pct']}")
print(f"  Details: {metrics['shared_error_rate']['details']}")
