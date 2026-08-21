"""Query Parser（Day 1 晚上）。

前置检索：纯规则解析 query 中的年份 + 指标关键词，避免每个 helper 各扫一遍 regex。
- 年份：4 位数字限定 2020–2029（避免 "4,527,498" / "P5" 等误匹配）
- 多年度：list[int] 保序去重；"2023-2025" / "2023-25" 都展开成完整 list
- "近三年"：依赖 corpus_years 参数（默认 None → 返回空，让 LLM/retrieval 自己处理）
- 指标：关键词表 + 最长匹配优先（"归母净利润" > "净利润"）

设计要点：
- 不调用 LLM；规则覆盖不到的情况由 intent_classify / query_rewrite 兜底
- 不读 DB；document_ids 由 pipeline 在 parser 之后单独解析（避免 parser 强耦合数据层）
- frozen=False：parser 阶段 dataclass 还未冻结，pipeline 可能后置补充字段
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from app.core.retrieval_filter import RetrievalFilter


# ── 年份提取 ──────────────────────────────────────────────

# 4 位数字限定 2020–2029（业务语料年份范围）；后续可改 corpus-aware
_YEAR_RANGE_START = 2020
_YEAR_RANGE_END = 2029

# 单年匹配：4 位数字，必须紧邻"年"字或被分隔（避免 "4527" 被误识）
_YEAR_PATTERN = re.compile(r"(20[2-9]\d)\s*年?")

# 范围匹配：2023-2025 / 2023-25
_YEAR_RANGE_PATTERN = re.compile(r"(20[2-9]\d)\s*[-–—~至到]\s*(20[2-9]\d|\d{2})")


def _expand_short_year(start: int, end_short: int) -> int:
    """2023-25 → 25 → 2025（短年份补全到同世纪）。"""
    century = (start // 100) * 100
    return century + end_short


def _expand_year_range(start_str: str, end_str: str) -> list[int]:
    start = int(start_str)
    if len(end_str) == 2:
        end = _expand_short_year(start, int(end_str))
    else:
        end = int(end_str)
    if end < start:
        start, end = end, start
    return list(range(start, end + 1))


def extract_years(query: str, corpus_years: Iterable[int] | None = None) -> list[int] | None:
    """从 query 提取年份（保序去重）。"近三年"等相对表达由 corpus_years 提供。

    Returns:
        list[int] 含 1+ 年份；None 表示未提取到任何年份。
    """
    if not query:
        return None

    found: list[int] = []
    seen: set[int] = set()

    # 1) 范围先匹配（避免范围被拆成单年）
    for m in _YEAR_RANGE_PATTERN.finditer(query):
        for y in _expand_year_range(m.group(1), m.group(2)):
            if _YEAR_RANGE_START <= y <= _YEAR_RANGE_END and y not in seen:
                seen.add(y)
                found.append(y)

    # 2) 单年匹配（跳过已被范围覆盖的位置）
    covered_spans = [(m.start(), m.end()) for m in _YEAR_RANGE_PATTERN.finditer(query)]
    for m in _YEAR_PATTERN.finditer(query):
        in_range = any(s <= m.start() < e for s, e in covered_spans)
        if in_range:
            continue
        y = int(m.group(1))
        if _YEAR_RANGE_START <= y <= _YEAR_RANGE_END and y not in seen:
            seen.add(y)
            found.append(y)

    # 3) "近三年" / "近 N 年" → 用 corpus_years
    if "近三年" in query or "近3年" in query:
        if corpus_years:
            cy = sorted(set(corpus_years))
            for y in cy:
                if y not in seen:
                    seen.add(y)
                    found.append(y)

    return found or None


# ── 指标提取 ──────────────────────────────────────────────

# 关键词表（按"特异性"排序：长的在前，长匹配优先）
_METRIC_KEYWORDS: list[str] = [
    "归母净利润", "扣非净利润", "扣非归母净利润",
    "经营活动产生的现金流量净额", "每股经营现金流",
    "研发投入", "研发费用", "研发投入情况",
    "营业收入", "营业总收入", "营收", "营业收入增长率",
    "净利润", "利润总额",
    "基本每股收益", "稀释每股收益", "每股收益",
    "总资产", "总负债", "净资产", "资产负债率",
    "现金分红", "分红方案", "利润分配", "股利",
    "员工人数", "在职员工", "员工",
    "专利申请", "发明专利", "专利",
    "主营业务收入", "主营业务",
    "国际业务", "国际营收",
    "应付职工薪酬", "应收账款",
]


def extract_metric(query: str) -> str | None:
    """最长匹配优先；返回最具体的指标。"""
    if not query:
        return None
    for kw in _METRIC_KEYWORDS:
        if kw in query:
            return kw
    return None


# ── ParsedQuery dataclass ────────────────────────────────


@dataclass
class ParsedQuery:
    """前置检索的查询意图快照（Day 1 晚上）。

    字段：
    - raw：原始 query 字符串（保留供下游 logger / 调试）
    - years：提取的年份（保序去重）；None 表示无显式年份
    - intent_metric：关键词命中的财务指标；None 表示无显式指标
    - document_ids：外部传入的候选 doc_ids（pipeline 阶段 year→doc_id 解析后回填）
    - section_name：当前 parser 不填，预留给未来扩展
    - filters：自动从 years/document_ids 构造的 RetrievalFilter，传给 retrieval
    """
    raw: str
    years: list[int] | None = None
    intent_metric: str | None = None
    document_ids: list[str] | None = None
    section_name: str | None = None
    filters: RetrievalFilter = field(default_factory=RetrievalFilter)


# ── 主入口 ────────────────────────────────────────────────


def parse_query(
    query: str,
    corpus_years: Iterable[int] | None = None,
    document_ids: Iterable[str] | None = None,
) -> ParsedQuery:
    """解析 query，返回 ParsedQuery。

    Args:
        query: 用户原始 query
        corpus_years: 语料覆盖的年份（用于解析 "近三年" 这类相对表达）；None 时不解析
        document_ids: pipeline 阶段已解析出的 doc_ids（parser 仅透传，不做 DB 查询）
    """
    if not query:
        return ParsedQuery(raw=query or "")

    years = extract_years(query, corpus_years=corpus_years)
    metric = extract_metric(query)
    doc_ids = list(document_ids) if document_ids else None

    # 自动构造 RetrievalFilter：years 和 document_ids 都填
    filters = RetrievalFilter(
        years=frozenset(years) if years else None,
        document_ids=frozenset(doc_ids) if doc_ids else None,
    )
    return ParsedQuery(
        raw=query,
        years=years,
        intent_metric=metric,
        document_ids=doc_ids,
        section_name=None,
        filters=filters,
    )
