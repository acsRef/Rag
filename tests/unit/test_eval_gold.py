"""eval.gold 单测 — gold loader + validator。

覆盖:
- load_testset / get_question / iter_questions
- GoldQuestion 属性 (category_prefix / is_verified_gold / should_answer)
- evidence_snippets
- validate schema 校验
"""

import pytest

from eval.gold import (
    GOLD_SOURCE_ORIGINAL,
    GOLD_SOURCE_VERIFIED,
    get_question,
    iter_questions,
    load_testset,
    validate,
)


@pytest.fixture(scope="module")
def ds():
    return load_testset()


# ── load / get ──────────────────────────────────────────────────────────


def test_load_testset_has_65_questions(ds):
    assert len(ds["题目"]) == 65


def test_get_question_q33(ds):
    q = get_question("Q33", ds)
    assert q.id == "Q33"
    assert q.category_prefix == "E"
    assert "2024年归母净利润" in q.question or "2024年报" in q.question


def test_get_question_missing_raises(ds):
    with pytest.raises(KeyError):
        get_question("Q999", ds)


# ── verified gold (Q26/Q55 corrections) ────────────────────────────────


def test_q26_verified_gold_contains_080(ds):
    """D 迁移后 Q26 gold 应为修正版 0.80 元/股."""
    q = get_question("Q26", ds)
    assert "0.80" in q.gold_answer
    assert q.is_verified_gold is True
    assert q.gold_source == GOLD_SOURCE_VERIFIED
    assert len(q.gold_evidence) >= 1


def test_q55_verified_gold_contains_864(ds):
    """D 迁移后 Q55 gold 应为 86.4 亿元 (原 gold 错说无法获得)."""
    q = get_question("Q55", ds)
    assert "86.4" in q.gold_answer
    assert q.is_verified_gold is True


def test_unverified_question_keeps_original_gold(ds):
    """未验证的 50 题 gold_source=original_unverified, evidence 为空."""
    q = get_question("Q05", ds)
    assert q.is_verified_gold is False
    assert q.gold_source == GOLD_SOURCE_ORIGINAL
    assert q.gold_evidence == []


# ── iter_questions filters ──────────────────────────────────────────────


def test_iter_questions_by_category_prefix(ds):
    a_qs = iter_questions(ds, category_prefix="A")
    assert len(a_qs) == 10
    assert all(q.category_prefix == "A" for q in a_qs)


def test_iter_questions_verified_only(ds):
    v_qs = iter_questions(ds, verified_only=True)
    assert len(v_qs) == 15
    assert all(q.is_verified_gold for q in v_qs)


# ── GoldQuestion properties ─────────────────────────────────────────────


def test_should_answer_from_gold_judgment(ds):
    q56 = get_question("Q56", ds)
    assert q56.should_answer is False  # not_answerable

    q26 = get_question("Q26", ds)
    assert q26.should_answer is True  # answerable

    q05 = get_question("Q05", ds)
    assert q05.should_answer is None  # 未标注


def test_evidence_snippets_returns_strings(ds):
    q26 = get_question("Q26", ds)
    snippets = q26.evidence_snippets()
    assert all(isinstance(s, str) and s for s in snippets)


# ── validate ────────────────────────────────────────────────────────────


def test_validate_real_testset_passes(ds):
    problems = validate(ds)
    assert problems == [], f"Unexpected validation problems: {problems}"


def test_validate_detects_missing_required_field():
    bad = {"题目": [{"id": "X01", "类别": "A-test", "问题": "", "参考答案": "ans"}]}
    problems = validate(bad)
    assert any("missing required field '问题'" in p for p in problems)


def test_validate_detects_duplicate_id():
    bad = {
        "题目": [
            {"id": "X01", "类别": "A-t", "问题": "q", "参考答案": "a"},
            {"id": "X01", "类别": "A-t", "问题": "q2", "参考答案": "a2"},
        ]
    }
    problems = validate(bad)
    assert any("duplicate id" in p for p in problems)


def test_validate_detects_bad_judgment():
    bad = {
        "题目": [
            {
                "id": "X01",
                "类别": "A-t",
                "问题": "q",
                "参考答案": "a",
                "gold_judgment": "maybe",
            }
        ]
    }
    problems = validate(bad)
    assert any("invalid gold_judgment" in p for p in problems)


def test_validate_detects_bad_evidence_structure():
    bad = {
        "题目": [
            {
                "id": "X01",
                "类别": "A-t",
                "问题": "q",
                "参考答案": "a",
                "gold_evidence": ["not-a-dict"],
            }
        ]
    }
    problems = validate(bad)
    assert any("gold_evidence[0] missing 'snippet'" in p for p in problems)
