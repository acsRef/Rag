"""eval/metrics.py 纯函数测试：Recall@k / MRR / Hit Rate。"""
import pytest

from eval.metrics import compute_all, hit_at_k, mrr, recall_at_k


# ── hit_at_k ────────────────────────────────────────────────


def test_hit_at_k_hit_inside_window():
    assert hit_at_k(["a", "b", "c"], ["c"], k=3) is True


def test_hit_at_k_miss_outside_window():
    assert hit_at_k(["a", "b"], ["c"], k=5) is False


def test_hit_at_k_empty_gold_returns_false():
    """无 gold 标注时不应当成命中——避免误报。"""
    assert hit_at_k(["a", "b"], [], k=5) is False


def test_hit_at_k_empty_retrieved_returns_false():
    assert hit_at_k([], ["a"], k=5) is False


def test_hit_at_k_k_smaller_than_position_returns_false():
    """gold 在第 3 位，k=2 应当 miss。"""
    assert hit_at_k(["x", "y", "a"], ["a"], k=2) is False


# ── recall_at_k ─────────────────────────────────────────────


def test_recall_at_k_full_hit():
    assert recall_at_k(["a", "b", "c"], ["a", "b", "c"], k=3) == 1.0


def test_recall_at_k_partial_hit():
    """3 个 gold，召回 2 个。"""
    assert recall_at_k(["a", "b", "c"], ["a", "c", "e"], k=5) == pytest.approx(2 / 3)


def test_recall_at_k_no_hit():
    assert recall_at_k(["x", "y"], ["a", "b"], k=5) == 0.0


def test_recall_at_k_empty_gold_returns_zero():
    assert recall_at_k(["a"], [], k=5) == 0.0


def test_recall_at_k_truncates_to_window():
    """gold 在第 5 位，k=3 → 0/1 = 0；k=5 → 命中。"""
    items = ["x", "x", "x", "x", "a"]
    assert recall_at_k(items, ["a"], k=3) == 0.0
    assert recall_at_k(items, ["a"], k=5) == 1.0


# ── mrr ─────────────────────────────────────────────────────


def test_mrr_first_position_is_one():
    assert mrr(["a", "b", "c"], ["a"]) == 1.0


def test_mrr_third_position():
    assert mrr(["x", "y", "a"], ["a"]) == pytest.approx(1 / 3)


def test_mrr_uses_first_gold_hit():
    """多个 gold，MRR 取首个命中的位置（不是最优）。"""
    assert mrr(["x", "a", "b"], ["a", "b"]) == 0.5


def test_mrr_no_hit_returns_zero():
    assert mrr(["x", "y"], ["a"]) == 0.0


def test_mrr_empty_gold_returns_zero():
    assert mrr(["a", "b"], []) == 0.0


# ── compute_all ─────────────────────────────────────────────


def test_compute_all_returns_all_metric_keys():
    out = compute_all(["a", "b", "c"], ["c"])
    assert set(out.keys()) == {"hit@5", "hit@10", "recall@5", "recall@10", "mrr"}


def test_compute_all_hit_and_recall_agree_on_full_hit():
    out = compute_all(["a", "b"], ["a"])
    assert out["hit@5"] is True
    assert out["recall@5"] == 1.0
    assert out["mrr"] == 1.0


def test_compute_all_hit5_true_hit10_true_when_in_window():
    out = compute_all(["a", "x"], ["a"])
    assert out["hit@5"] is True
    assert out["hit@10"] is True


def test_compute_all_no_gold_yields_zero_metrics():
    """compute_all 不报错，指标全为 0/False（容许 gold 缺失场景）。"""
    out = compute_all(["a", "b"], [])
    assert out["hit@5"] is False
    assert out["hit@10"] is False
    assert out["recall@5"] == 0.0
    assert out["recall@10"] == 0.0
    assert out["mrr"] == 0.0
