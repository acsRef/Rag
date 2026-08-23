"""Judge rubric + calibration constants — evaluator 语义的单一来源。

**Rubric v1** (per docs/plans/2026-08-23-judge-rubric-v1.md):
- Correct: 核心结论与关键事实均一致
- Partial: 核心结论正确，但存在遗漏 / 数值不完整 / 部分维度错误
- Wrong: 核心答案错误且构成主要结论 / misattribution / false refusal / hallucination

**关键判据**: 错误 claim 是否构成主要结论
- 问题问什么，主体就必须答什么
- 直接回答的部分错了 → Wrong（即使背景/原因/修饰对）
- 直接回答的部分对，只是细节缺失 → Partial

本模块是 pure constants + 纯函数，无 LLM 依赖。
judge prompt 组装、verdict 归并、三轴指标计算都从这里取语义。
"""

from __future__ import annotations

# ── Verdict 枚举 ────────────────────────────────────────────────────────

CORRECT = "correct"
PARTIAL = "partial"
WRONG = "wrong"
UNKNOWN = "unknown"

# judge 0-3 分 → verdict 的归并表
SCORE_TO_VERDICT: dict[int | None, str] = {
    3: CORRECT,
    2: PARTIAL,
    1: WRONG,
    0: WRONG,
    None: UNKNOWN,
}


def score_to_verdict(score: int | None) -> str:
    """judge 分数 → 三档 verdict。"""
    return SCORE_TO_VERDICT.get(score, UNKNOWN)


# ── Collapse 规则 (用于 accuracy 计算) ──────────────────────────────────


def collapse(verdict: str) -> str:
    """三档 → 二档 (right/wrong)，用于 Judge↔Reality accuracy 计算。

    partial 归入 right（方向对即 right）— 与 gold-correction report 口径一致。
    """
    if verdict in (CORRECT, PARTIAL):
        return "right"
    if verdict == WRONG:
        return "wrong"
    return "unknown"


# ── Rubric 文本（judge prompt 注入用） ───────────────────────────────────

RUBRIC_TEXT = """评分标准：
- 3分：完全正确。核心结论与关键事实均一致——数字、单位、口径、时间全部正确，允许表述差异。
- 2分：基本正确。核心结论正确，但存在遗漏、数值不完整或部分维度错误（方向对、主体数字对）。
- 1分：部分正确但核心有误，或关键数字张冠李戴（把 A 年份/指标的数据说成 B 的）。
- 0分：错误/编造数据/错误拒答。

关键判据：回答问题的主体部分是否正确。
- 若直接回答问题的数字/结论错误，即使原因分析正确，也判 0-1 分（不构成主要结论的正确性）。
- 若模型声称"未披露"或"无法获得"，但参考答案表明数据存在，判 0 分（错误拒答）。"""

# ── 三轴指标 (gold-correction-report §3) ────────────────────────────────


def agreement_judge_human(
    pairs: list[tuple[str, str]],
) -> dict:
    """Metric 1: Judge ↔ Human raw agreement（不分对错）。

    Args:
        pairs: [(judge_verdict, human_verdict), ...]

    Returns:
        {matches, total, pct, mismatches}
    """
    total = len(pairs)
    matches = sum(1 for j, h in pairs if j == h)
    mismatches = [{"judge": j, "human": h} for j, h in pairs if j != h]
    return {
        "matches": matches,
        "total": total,
        "pct": matches / total if total else None,
        "mismatches": mismatches,
    }


def accuracy_vs_reality(
    judge_verdicts: list[str],
    reality_verdicts: list[str],
) -> dict:
    """Metric 2: Judge ↔ Reality accuracy（collapse 到二档）。

    Args:
        judge_verdicts: 与 reality 一一对应的 judge verdict 列表
        reality_verdicts: verified ground truth 列表

    Returns:
        {matches, total, pct, errors} — errors 为 judge≠reality 的明细
    """
    assert len(judge_verdicts) == len(reality_verdicts)
    total = len(judge_verdicts)
    errors = []
    matches = 0
    for j, r in zip(judge_verdicts, reality_verdicts):
        jc, rc = collapse(j), collapse(r)
        if jc == rc and jc != "unknown":
            matches += 1
        else:
            errors.append({"judge": j, "reality": r})
    return {
        "matches": matches,
        "total": total,
        "pct": matches / total if total else None,
        "errors": errors,
    }


def shared_errors(
    triples: list[tuple[str, str, str]],
) -> dict:
    """Metric 3: Shared Error Rate — judge 错 AND human 错。

    用于发现 "agreement on a wrong verdict"（如原 Q26/Q55: gold 错导致
    judge+human 共同错）。

    Args:
        triples: [(judge_verdict, human_verdict, reality_verdict), ...]

    Returns:
        {shared_wrong, total, rate, cases}
        shared_wrong = judge collapse=wrong AND human=wrong (无论 reality)
        — caller 可结合 reality 区分 real error vs gold-induced error
    """
    total = len(triples)
    cases = []
    for j, h, r in triples:
        if collapse(j) == "wrong" and h == WRONG:
            cases.append({"judge": j, "human": h, "reality": r})
    # Real shared errors: 双方都错且 reality 确实错（排除 gold-induced）
    real = [c for c in cases if c["reality"] == WRONG]
    return {
        "shared_wrong": len(cases),
        "real_shared_wrong": len(real),
        "gold_induced": len(cases) - len(real),
        "total": total,
        "rate": len(cases) / total if total else None,
        "cases": cases,
    }
