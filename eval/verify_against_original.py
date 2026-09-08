"""PDF原文交叉验证 — 独立核对 human labels。

不依赖 RAG / vector DB / Judge。直接读 PDF 提取文本，按问题 ID 搜原文，
输出 original_snippet + human_verdict + verdict_match。

Usage: PYTHONPATH=. D:/miniConda/envs/rag/python.exe eval/verify_against_original.py
"""

import json
import re
from pathlib import Path

# 三个 PDF 的 markdown 提取
PDF_MD = {
    2023: Path("eval/sany_annual_reports/三一重工_2023年年度报告.pymupdf4llm.md"),
    2024: Path("eval/sany_annual_reports/三一重工_2024年年度报告.pymupdf4llm.md"),
    2025: Path("eval/sany_annual_reports/三一重工_2025年年度报告.pymupdf4llm.md"),
}

# 加载 markdown
pdf_text = {year: path.read_text(encoding="utf-8") for year, path in PDF_MD.items()}
print(f"Loaded {len(pdf_text)} PDFs ({[(y, len(t)) for y, t in pdf_text.items()]})")
print()


# 用户提供的 15 题 human labels
QUESTIONS = [
    ("Q01", "A", "2023年的营业收入是多少", "732.22亿元"),
    ("Q02", "A", "2024年归属于上市公司股东的净利润", "59.75亿元"),
    ("Q03", "A", "2025年经营活动产生的现金流量净额", "199.75亿元（19,975,261千元）"),
    ("Q11", "B", "表格", None),  # 通用 — 用具体关键词
    ("Q12", "B", "2025年末总资产", "1,733.0亿元（173,299,138千元）"),
    ("Q17", "C", "跨文档对比", None),
    ("Q18", "C", "跨文档对比", None),
    ("Q25", "D", "每股合计分红", None),
    (
        "Q26",
        "D",
        "2025年每股合计分红（含已实施的中期分红与年度预案）",
        "全年合计每10股4.9元，即每股0.49元",
    ),
    ("Q32", "E", "时序与追溯调整", None),
    ("Q33", "E", "时序与追溯调整", None),
    ("Q50", "H", "错误前提纠偏", None),
    ("Q51", "H", "错误前提纠偏", None),
    ("Q55", "I", "新能源（电动化）产品收入", "无法从年报中获得"),
    ("Q56", "I", "2026年的营业收入增长目标", "三份年报中没有可回答的数字"),
]

HUMAN_LABELS = {
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


# 每题的搜索关键词
SEARCH_KEYWORDS = {
    "Q01": ["732", "740.19", "营业收入", "2023年"],
    "Q02": ["59.75", "5,975,451", "归属于上市公司股东的净利润"],
    "Q03": ["19,975,261", "199.75", "经营活动产生的现金流量"],
    "Q11": ["25,930", "2,445", "在职员工", "员工人数"],
    "Q12": ["173,299,138", "1,733", "总资产"],
    "Q17": ["净利润", "趋势", "近三年", "增长"],
    "Q18": ["跨", "对比", "国际", "海外"],
    "Q25": ["分红", "每股", "10股"],
    "Q26": ["分红", "每股", "10股", "0.49", "0.18", "4.9"],
    "Q32": ["调整", "追溯", "时序", "重述"],
    "Q33": ["调整", "追溯", "时序", "重述"],
    "Q50": ["研发", "投入", "增加", "上涨"],
    "Q51": ["研发", "投入", "下降", "增长"],
    "Q55": ["新能源", "电动化", "86.4", "115"],
    "Q56": ["2026", "增长目标", "经营计划", "未来"],
}


def search_in_pdf(year: int, keywords: list[str], context_chars: int = 200) -> list[dict]:
    """在指定 PDF 中搜索关键词，返回匹配片段列表。"""
    text = pdf_text[year]
    hits = []
    for kw in keywords:
        for m in re.finditer(re.escape(kw), text):
            start = max(0, m.start() - context_chars)
            end = min(len(text), m.end() + context_chars)
            snippet = text[start:end].replace("\n", " ").strip()
            hits.append(
                {
                    "year": year,
                    "keyword": kw,
                    "position": m.start(),
                    "snippet": snippet,
                }
            )
            if len(hits) >= 5:  # 限制每个 keyword 最多 5 个 hit
                break
    return hits[:10]  # 限制总 hit 数


def verify_q(qid: str, q_desc: str, gold: str | None) -> dict:
    keywords = SEARCH_KEYWORDS.get(qid, [])
    all_hits = []
    for year in [2023, 2024, 2025]:
        all_hits.extend(search_in_pdf(year, keywords))
    return {
        "qid": qid,
        "description": q_desc,
        "gold": gold,
        "pdf_hits_count": len(all_hits),
        "pdf_hits": all_hits[:5],  # Top 5 hits
    }


print("=" * 80)
print("PDF 原文交叉验证（不依赖 RAG / Vector DB / Judge）")
print("=" * 80)
print()

for qid, cat, desc, gold in QUESTIONS:
    ver = verify_q(qid, desc, gold)
    human = HUMAN_LABELS.get(qid, {})
    judge_score = json.loads(
        Path(f"eval/judge_calibration_v3/{qid}.json").read_text(encoding="utf-8")
    )["judge_score"]
    rag_answer = json.loads(
        Path(f"eval/judge_calibration_v3/{qid}.json").read_text(encoding="utf-8")
    )["generation_answer"]

    print(f"\n{'─' * 80}")
    print(f"## {qid} [{cat}] — {desc}")
    print(
        f"  Human: correct={human.get('correct')} partial={human.get('partial')} | Judge: {judge_score}/3"
    )
    print(f"  Gold: {gold or '(see Q record)'}")
    print(f"  RAG answer ({len(rag_answer)} chars): {rag_answer[:200]}")
    print(f"  PDF hits: {ver['pdf_hits_count']}")

    # 显示 top 3 hits
    for i, hit in enumerate(ver["pdf_hits"][:3]):
        print(f"  [{i + 1}] {hit['year']}年报 / kw='{hit['keyword']}':")
        print(f"      ...{hit['snippet'][:250]}...")
