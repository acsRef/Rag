"""三一重工年报 RAG 测试集自动标注工具（Day 1 下午 P1 收尾）。

基于"答案依据"字段扫描年份（2023/2024/2025），映射到 document_id，
作为每题的 gold_documents 字段写回 rag_testset.json。

设计要点：
- 单一正则提取三份年报的年份，覆盖 "X年年度报告" / "X 年年度报告" / 跨多份分隔符（/ vs 、）
- 已知 doc_id 硬编码（避免重跑依赖 DB 连接）：DB 一次查询后保存即可
- "文档集外信息" / 空答案依据 → 空 gold_documents（I 类拒答题的正确语义）
- 输出新文件 eval/sany_annual_reports/rag_testset.gold.json 让你 diff，OK 后再覆盖原文件

用法：
    D:/miniConda/envs/rag/python.exe tools/annotate_gold_documents.py            # 写新文件 diff
    D:/miniConda/envs/rag/python.exe tools/annotate_gold_documents.py --apply    # 覆盖原文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# DB 一次查到的映射（来自 Document 表）；硬编码避免每次重跑连 DB
YEAR_TO_DOC_ID: dict[int, str] = {
    2023: "c70ad0ddcb184c55",
    2024: "f4b0be8aaa644522",
    2025: "aebd3be225b8471f",
}

# "答案依据"中"2023年年度报告" / "2024 年报" / "2023年报" 等的年份提取。
# 原文里既有 "2023年度报告"（单年）也有 "2023年年度报告"（双年）；用宽松版本。
_YEAR_PATTERN = re.compile(r"(2023|2024|2025)\s*年\s*(?:年度报告|报)")


def extract_years_from_basis(basis: str) -> list[int]:
    """从'答案依据'字符串中提取出现的年份（保序，去重）。"""
    seen: set[int] = set()
    ordered: list[int] = []
    for m in _YEAR_PATTERN.finditer(basis):
        year = int(m.group(1))
        if year not in seen:
            seen.add(year)
            ordered.append(year)
    return ordered


def annotate_question(q: dict) -> tuple[list[str], list[str]]:
    """返回 (gold_documents, warnings)。

    规则：
    1. "答案依据"扫到年份 → 用这些年份映射到 doc_id
    2. 空答案依据 / "文档集外信息" / "无...披露" → 空 gold_documents
    3. 答案依据完全没有年份提及 → 扫"问题"字段兜底
    4. 问题字段也没有 → warning + 空 gold
    """
    warnings: list[str] = []
    basis = (q.get("答案依据") or "").strip()
    qid = q.get("id", "?")

    # 显式无答案的场景（拒答 / 文档外）
    if any(hint in basis for hint in ("文档集外信息", "文档外", "无披露", "无相关")):
        return [], []

    years = extract_years_from_basis(basis)
    if not years and basis:
        warnings.append(f"{qid}: 答案依据='{basis[:40]}' 未识别年份")

    if not years:
        # 兜底：扫"问题"字段
        question_text = q.get("问题") or ""
        years = extract_years_from_basis(question_text)
        if years:
            warnings.append(f"{qid}: 答案依据无年份，从'问题'字段兜底提取 {years}")

    if not years:
        warnings.append(f"{qid}: 完全无年份信息 → 空 gold_documents")

    gold_doc_ids = [YEAR_TO_DOC_ID[y] for y in years if y in YEAR_TO_DOC_ID]
    return gold_doc_ids, warnings


def annotate_testset(testset: dict) -> tuple[dict, list[str]]:
    """批量标注，返回 (新 testset, 全部 warnings)。"""
    new_questions = []
    all_warnings: list[str] = []
    gold_count: dict[int, int] = {1: 0, 2: 0, 3: 0, 0: 0}  # 各 gold 文档数对应的题数

    for q in testset["题目"]:
        q_new = dict(q)  # 浅拷贝，保留所有原字段
        gold, warns = annotate_question(q)
        q_new["gold_documents"] = gold
        new_questions.append(q_new)
        all_warnings.extend(warns)
        gold_count[len(gold)] = gold_count.get(len(gold), 0) + 1

    new_testset = dict(testset)
    new_testset["题目"] = new_questions
    summary = (
        f"\n[summary] 共 {len(new_questions)} 题，gold 文档数分布：\n"
        + "\n".join(f"  {k} 个 gold: {v} 题" for k, v in sorted(gold_count.items()))
    )
    if all_warnings:
        all_warnings.append(summary)
    else:
        all_warnings.append(summary)
    return new_testset, all_warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="直接覆盖原文件；不加则写到 rag_testset.gold.json 让你 diff",
    )
    parser.add_argument(
        "--testset",
        default="eval/sany_annual_reports/rag_testset.json",
    )
    args = parser.parse_args()

    testset_path = Path(args.testset)
    with open(testset_path, encoding="utf-8") as f:
        testset = json.load(f)

    new_testset, warnings = annotate_testset(testset)

    if args.apply:
        out_path = testset_path
    else:
        out_path = testset_path.parent / (testset_path.stem + ".gold.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(new_testset, f, ensure_ascii=False, indent=2)

    print(f"写入 {out_path}（{len(new_testset['题目'])} 题）")
    if warnings:
        print("\n=== warnings / summary ===")
        for w in warnings:
            print(w)


if __name__ == "__main__":
    main()
