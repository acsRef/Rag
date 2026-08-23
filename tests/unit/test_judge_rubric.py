"""eval.judge_rubric 单测 — rubric v1 + 三轴指标。

用 P0 calibration 的真实数据做 fixture（Q33 rubric 案例 / Q26+Q55 shared error）。
"""

from eval.judge_rubric import (
    CORRECT,
    PARTIAL,
    UNKNOWN,
    WRONG,
    accuracy_vs_reality,
    agreement_judge_human,
    collapse,
    score_to_verdict,
    shared_errors,
)

# ── score_to_verdict ────────────────────────────────────────────────────


def test_score_mapping():
    assert score_to_verdict(3) == CORRECT
    assert score_to_verdict(2) == PARTIAL
    assert score_to_verdict(1) == WRONG
    assert score_to_verdict(0) == WRONG
    assert score_to_verdict(None) == UNKNOWN


# ── collapse ────────────────────────────────────────────────────────────


def test_collapse_rules():
    assert collapse(CORRECT) == "right"
    assert collapse(PARTIAL) == "right"  # partial 归 right (gold-correction 口径)
    assert collapse(WRONG) == "wrong"
    assert collapse(UNKNOWN) == "unknown"


# ── Metric 1: Agreement ─────────────────────────────────────────────────


def test_agreement_perfect():
    r = agreement_judge_human([(CORRECT, CORRECT), (WRONG, WRONG)])
    assert r["matches"] == 2
    assert r["pct"] == 1.0
    assert r["mismatches"] == []


def test_agreement_q33_case():
    """Q33: judge=wrong, human=partial → mismatch (即使 reality 也是 wrong)."""
    r = agreement_judge_human([(WRONG, PARTIAL)])
    assert r["matches"] == 0
    assert r["mismatches"] == [{"judge": WRONG, "human": PARTIAL}]


def test_agreement_real_calibration_14_of_15():
    """复现 gold-correction report 的 Metric 1 = 93.3% (14/15)."""
    pairs = [
        (PARTIAL, PARTIAL),  # Q01
        (CORRECT, CORRECT),  # Q02
        (CORRECT, CORRECT),  # Q03
        (PARTIAL, PARTIAL),  # Q11
        (WRONG, WRONG),  # Q12
        (WRONG, WRONG),  # Q17
        (PARTIAL, PARTIAL),  # Q18
        (CORRECT, CORRECT),  # Q25
        (WRONG, WRONG),  # Q26 (shared error on wrong verdict)
        (PARTIAL, PARTIAL),  # Q32
        (WRONG, PARTIAL),  # Q33 ← 唯一 mismatch
        (CORRECT, CORRECT),  # Q50
        (PARTIAL, PARTIAL),  # Q51
        (WRONG, WRONG),  # Q55 (shared error)
        (CORRECT, CORRECT),  # Q56
    ]
    r = agreement_judge_human(pairs)
    assert r["matches"] == 14
    assert abs(r["pct"] - 14 / 15) < 1e-9


# ── Metric 2: Accuracy vs Reality ───────────────────────────────────────


def test_accuracy_q26_gold_induced_error():
    """Q26: judge 说 wrong 但 RAG 实际 correct (原 gold 错) → judge error."""
    r = accuracy_vs_reality([WRONG], [CORRECT])
    assert r["matches"] == 0
    assert r["errors"] == [{"judge": WRONG, "reality": CORRECT}]


def test_accuracy_partial_reality_counts_right():
    """reality=partial 时 judge=partial 也算 match."""
    r = accuracy_vs_reality([PARTIAL], [PARTIAL])
    assert r["matches"] == 1


# ── Metric 3: Shared Errors ─────────────────────────────────────────────


def test_shared_errors_distinguishes_gold_induced():
    """核心场景: Q12/Q17 是 real shared errors; Q26/Q55 是 gold-induced."""
    triples = [
        # (judge, human, reality)
        (WRONG, WRONG, WRONG),  # Q12: real shared error
        (WRONG, WRONG, WRONG),  # Q17: real shared error
        (WRONG, WRONG, CORRECT),  # Q26: gold-induced (RAG 其实对)
        (WRONG, WRONG, CORRECT),  # Q55: gold-induced
    ]
    r = shared_errors(triples)
    assert r["shared_wrong"] == 4
    assert r["real_shared_wrong"] == 2
    assert r["gold_induced"] == 2
    assert abs(r["rate"] - 1.0) < 1e-9


def test_shared_errors_empty_when_either_right():
    r = shared_errors(
        [
            (WRONG, PARTIAL, WRONG),  # human 对 → not shared
            (PARTIAL, WRONG, WRONG),  # judge(partial→right) 对 → not shared
            (CORRECT, CORRECT, CORRECT),
        ]
    )
    assert r["shared_wrong"] == 0
