"""Gold loader + validator — rag_testset.json 的唯一读取入口。

**单一 source of truth 原则** (per user decision D, 2026-08-23):
所有 eval / regression / calibration 脚本都应通过本模块读取 gold，
不允许直接 open(".../rag_testset.json")。

Schema (rag_testset.json v2):
    测试集名称 / 版本 / 生成日期 / 语料文档 / 题目数量 / 字段说明
    题目[]:
        id, 类别, 题型, 难度, 问题,
        参考答案            # gold_answer (Q26/Q55 已修正)
        答案依据, 考察的RAG易错点, 常见错误答案, gold_documents,
        gold_evidence       # [{year, keyword, snippet}] — PDF 原文证据
        gold_judgment       # answerable / partial_answerable / not_answerable
        gold_source         # human_verified_2026-08-23 | original_unverified

Usage:
    from eval.gold import load_testset, get_question, iter_questions

    ds = load_testset()
    q33 = get_question("Q33")
    for q in iter_questions(category_prefix="A"):
        ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

TESTSET_PATH = Path(__file__).parent / "sany_annual_reports" / "rag_testset.json"

GOLD_SOURCE_VERIFIED = "human_verified_2026-08-23"
GOLD_SOURCE_ORIGINAL = "original_unverified"


@dataclass
class GoldQuestion:
    """单题 gold 数据（schema v2）。"""

    id: str
    category: str
    question_type: str
    difficulty: str
    question: str
    gold_answer: str  # 参考答案
    evidence_basis: str  # 答案依据
    pitfall: str  # 考察的RAG易错点
    common_wrong_answers: str
    gold_documents: list[str]
    # ── schema v2 新增 ──
    gold_evidence: list[dict] = field(default_factory=list)
    gold_judgment: str = ""  # answerable / partial_answerable / not_answerable
    gold_source: str = GOLD_SOURCE_ORIGINAL

    @property
    def category_prefix(self) -> str:
        return self.category.split("-")[0].strip() if self.category else ""

    @property
    def is_verified_gold(self) -> bool:
        return self.gold_source == GOLD_SOURCE_VERIFIED

    @property
    def should_answer(self) -> bool | None:
        """gold_judgment 推断的 should_answer；未标注时返回 None。"""
        if not self.gold_judgment:
            return None
        return self.gold_judgment != "not_answerable"

    def evidence_snippets(self) -> list[str]:
        """gold_evidence 的纯文本列表（喂给 judge / deterministic checks）。"""
        return [e.get("snippet", "") for e in self.gold_evidence]


def _parse_question(raw: dict) -> GoldQuestion:
    return GoldQuestion(
        id=raw["id"],
        category=raw["类别"],
        question_type=raw.get("题型", ""),
        difficulty=raw.get("难度", ""),
        question=raw["问题"],
        gold_answer=raw["参考答案"],
        evidence_basis=raw.get("答案依据", ""),
        pitfall=raw.get("考察的RAG易错点", ""),
        common_wrong_answers=raw.get("常见错误答案", ""),
        gold_documents=raw.get("gold_documents", []),
        gold_evidence=raw.get("gold_evidence", []),
        gold_judgment=raw.get("gold_judgment", ""),
        gold_source=raw.get("gold_source", GOLD_SOURCE_ORIGINAL),
    )


def load_testset(path: Path = TESTSET_PATH) -> dict:
    """加载完整测试集（含元数据）。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def iter_questions(
    ds: dict | None = None,
    category_prefix: str | None = None,
    verified_only: bool = False,
) -> list[GoldQuestion]:
    """迭代题目，支持按类别前缀 / verified 过滤。"""
    if ds is None:
        ds = load_testset()
    out = []
    for raw in ds["题目"]:
        q = _parse_question(raw)
        if category_prefix and q.category_prefix != category_prefix:
            continue
        if verified_only and not q.is_verified_gold:
            continue
        out.append(q)
    return out


def get_question(qid: str, ds: dict | None = None) -> GoldQuestion:
    """按 ID 取单题。KeyError if missing."""
    if ds is None:
        ds = load_testset()
    for raw in ds["题目"]:
        if raw["id"] == qid:
            return _parse_question(raw)
    raise KeyError(f"Question {qid} not found in {TESTSET_PATH}")


def validate(ds: dict) -> list[str]:
    """Schema 校验，返回问题列表（空 = 通过）。

    检查项：
    - 必填字段存在且非空
    - gold_source 取值合法
    - gold_judgment 取值合法（允许空 = 未标注）
    - gold_evidence 结构正确
    - id 唯一
    """
    problems = []
    seen_ids = set()
    valid_sources = {GOLD_SOURCE_VERIFIED, GOLD_SOURCE_ORIGINAL}
    valid_judgments = {"answerable", "partial_answerable", "not_answerable"}

    for raw in ds["题目"]:
        qid = raw.get("id", "?")
        if qid in seen_ids:
            problems.append(f"{qid}: duplicate id")
        seen_ids.add(qid)

        for key in ("id", "类别", "问题", "参考答案"):
            if not raw.get(key):
                problems.append(f"{qid}: missing required field '{key}'")

        source = raw.get("gold_source", "")
        if source and source not in valid_sources:
            problems.append(f"{qid}: invalid gold_source '{source}'")

        judgment = raw.get("gold_judgment", "")
        if judgment and judgment not in valid_judgments:
            problems.append(f"{qid}: invalid gold_judgment '{judgment}'")

        evidence = raw.get("gold_evidence", [])
        if not isinstance(evidence, list):
            problems.append(f"{qid}: gold_evidence must be a list")
        elif evidence:
            for i, e in enumerate(evidence):
                if not isinstance(e, dict) or "snippet" not in e:
                    problems.append(f"{qid}: gold_evidence[{i}] missing 'snippet'")

    return problems


# ── CLI self-check ──────────────────────────────────────────────────────

if __name__ == "__main__":
    ds = load_testset()
    problems = validate(ds)
    questions = iter_questions(ds)
    verified = [q for q in questions if q.is_verified_gold]

    print(f"Testset: {ds.get('测试集名称')} v{ds.get('版本')}")
    print(f"Questions: {len(questions)}")
    print(f"Verified gold: {len(verified)}")
    print(f"With evidence: {sum(1 for q in questions if q.gold_evidence)}")
    by_cat: dict[str, int] = {}
    for q in questions:
        by_cat[q.category_prefix] = by_cat.get(q.category_prefix, 0) + 1
    print(f"Categories: {dict(sorted(by_cat.items()))}")
    print()

    if problems:
        print(f"VALIDATION FAILED ({len(problems)} problems):")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    print("Validation PASSED")
