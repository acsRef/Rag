"""Deterministic numeric claim verification — B 层第一块。

设计原则（per user spec, 2026-08-23）：
- 不是 "数字出现在 PDF → 正确"（会产生 FP: 净利润 1733 vs 总资产 1733）
- 而是 claim-level: (value, unit, entity/metric context) 三元组验证

两层检查：
1. **Direct hit**: 数值+单位在 gold_evidence / PDF 中直接出现
2. **Arithmetic derivation**: 数值是 evidence 中两个数的和/差/换算
   （如 Q26: 0.80 = 0.31 + 0.49; Q25: 558.6 = 238.9 + 125 + 111.6 + 83.1）

本模块 pure function，无 LLM/DB 依赖。Phase B 只做 numeric；
citation check 独立模块。

Usage:
    from eval.deterministic_numeric import verify_number_claims
    results = verify_number_claims(answer_text, evidence_snippets)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 数值提取: 匹配 "数字(可含千分位逗号)+可选中文数量级+可选单位"
# 无单位也捕获 (如表格里的 8,408,057), unit 为空串
_NUM_PATTERN = re.compile(
    r"(?P<num>-?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<magnitude>万亿|千万|百万|十亿|亿|万)?"
    r"(?P<unit>元|%|个百分点|股|件|人|名|台)?"
)


@dataclass
class ClaimVerdict:
    """单个数值 claim 的验证结果。"""

    raw: str  # 原文片段
    value: float  # 解析出的数值（统一为基本单位）
    unit: str
    status: str  # "direct_hit" | "derived" | "not_found"
    derived_from: list[str] = field(default_factory=list)  # derivation 来源


def _normalize(num_str: str, magnitude: str | None) -> float:
    """把 '86.4亿' → 8640000000; '3,173' → 3173."""
    v = float(num_str.replace(",", ""))
    mult = {
        "万亿": 1e12,
        "千万": 1e7,
        "百万": 1e6,
        "十亿": 1e9,
        "亿": 1e8,
        "万": 1e4,
        None: 1,
    }
    return v * mult.get(magnitude or None, 1)


# 年份模式 (过滤: 年份不是 metric claim)
_YEAR_PATTERN = re.compile(r"^(19|20)\d{2}$")


def _is_year(v: float) -> bool:
    """1990-2099 的裸整数视为年份, 不作为 claim."""
    return 1990 <= v <= 2099 and v == int(v)


def _extract_numbers(text: str) -> list[tuple[float, str]]:
    """从文本提取所有数值 → [(normalized_value, unit)]. unit 可为空串.

    过滤: 裸年份整数 (2025 等) 不提取 — 它们是时间限定词不是数值 claim.
    """
    out = []
    for m in _NUM_PATTERN.finditer(text):
        try:
            raw = m.group("num").replace(",", "")
            # 年份过滤: 无小数、无 magnitude、无 unit 的 19xx/20xx
            if (
                "." not in raw
                and not m.group("magnitude")
                and not m.group("unit")
                and _YEAR_PATTERN.match(m.group("num"))
            ):
                continue
            v = _normalize(m.group("num"), m.group("magnitude"))
            out.append((v, m.group("unit") or ""))
        except ValueError:
            continue
    return out


def verify_number_claims(
    answer_text: str,
    evidence_texts: list[str],
    rel_tolerance: float = 0.005,
) -> list[ClaimVerdict]:
    """验证 answer 中每个数值 claim 是否被 evidence 支撑。

    两层逻辑:
    1. direct_hit: value 与 evidence 某数值相对误差 < tolerance
       （容忍 千元→亿元 单位差异: evidence 可能以千元计, answer 以亿元表述）
    2. derived: value = evidence 两数之和或差 (±tolerance)
       覆盖 Q26 类 (0.80 = 0.31 + 0.49) 与 Q25 类合计场景

    Args:
        answer_text: RAG 生成的答案全文
        evidence_texts: gold_evidence snippets 或 retrieved chunks 文本列表
        rel_tolerance: 相对容差 (default 0.5%, 吸收四舍五入)

    Returns:
        每个 answer 数值一个 ClaimVerdict。
    """
    evidence_nums = []
    for t in evidence_texts:
        evidence_nums.extend(_extract_numbers(t))

    verdicts = []
    for num_val, unit in _extract_numbers(answer_text):
        # Layer 1: direct hit
        hit = any(
            abs(num_val - ev) <= rel_tolerance * max(abs(ev), 1.0)
            for ev, u in evidence_nums
            if u == unit or _same_dimension(unit, u)
        )
        if hit:
            verdicts.append(ClaimVerdict(raw="", value=num_val, unit=unit, status="direct_hit"))
            continue

        # Layer 2: pairwise sum/diff derivation + per-share 换算 (X元/N股 → X/N 元每股)
        derived_from = []
        n = len(evidence_nums)
        found = False
        for i in range(n):
            for j in range(i + 1, n):
                vi, ui = evidence_nums[i]
                vj, uj = evidence_nums[j]
                if not (_same_dimension(unit, ui) and _same_dimension(unit, uj)):
                    continue
                if abs((vi + vj) - num_val) <= rel_tolerance * max(abs(num_val), 1.0):
                    derived_from = [f"{vi}+{vj}"]
                    found = True
                    break
                if abs((vi - vj) - num_val) <= rel_tolerance * max(abs(num_val), 1.0):
                    derived_from = [f"{vi}-{vj}"]
                    found = True
                    break
                # per-share: evidence "3.1元/10股" 提取为 (3.1, 元), answer "0.31元"
                # 3.1 / 10 = 0.31 — 除数通常是股数基数
                for div in (10, 100, 1000):
                    if abs((vi / div) - num_val) <= rel_tolerance * max(abs(num_val), 1e-9):
                        derived_from = [f"{vi}/{div}"]
                        found = True
                        break
                    if abs((vj / div) - num_val) <= rel_tolerance * max(abs(num_val), 1e-9):
                        derived_from = [f"{vj}/{div}"]
                        found = True
                        break
                if found:
                    break
            if found:
                break

        status = "derived" if found else "not_found"
        verdicts.append(
            ClaimVerdict(raw="", value=num_val, unit=unit, status=status, derived_from=derived_from)
        )

    return verdicts


def _same_dimension(u1: str, u2: str) -> bool:
    """单位维度归组。空串(无单位)与任何维度可比 — 表格数字通常省略单位."""
    if not u1 or not u2:
        return True
    groups = [
        {"元", "股"},  # magnitude 前缀已并入数值; 元/股 同为货币化基础单位
        {"%"},
        {"个百分点"},
        {"件"},
        {"人", "名"},
        {"台"},
    ]
    return any(u1 in g and u2 in g for g in groups)


def summarize(verdicts: list[ClaimVerdict]) -> dict:
    """聚合为可入 JSON 的 summary."""
    total = len(verdicts)
    by_status = {"direct_hit": 0, "derived": 0, "not_found": 0}
    for v in verdicts:
        by_status[v.status] += 1
    supported = by_status["direct_hit"] + by_status["derived"]
    return {
        "total_claims": total,
        "supported": supported,
        "unsupported": by_status["not_found"],
        "support_rate": round(supported / total, 3) if total else None,
        "by_status": by_status,
    }
