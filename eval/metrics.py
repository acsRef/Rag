"""Retrieval-only evaluation metrics (Day 1 上午)。

纯函数，无 LLM / DB 依赖。retrieval_eval.py 调用，ablation 报告复用。

设计要点：
- `compute_all` 是入口；其他函数单独导出便于 unit test 与 ablation 报告按需组合
- 空 gold 视作"未标注"：指标返回 0/False，不抛错——smoke 阶段 testset 没标 gold 时
  也能跑出 retrieval_only 跑通证据（items 数 + top1 doc）
"""
from typing import Sequence


def hit_at_k(retrieved_ids: Sequence[str], gold_ids: Sequence[str], k: int) -> bool:
    """top-k 任一 ID 命中 gold 即 True。空 gold 一律 False（避免误报）。"""
    if not gold_ids:
        return False
    gold = set(gold_ids)
    return any(rid in gold for rid in retrieved_ids[:k])


def recall_at_k(retrieved_ids: Sequence[str], gold_ids: Sequence[str], k: int) -> float:
    """Recall@k = (top-k 命中的 gold 数) / (gold 总数)。空 gold → 0.0。"""
    if not gold_ids:
        return 0.0
    gold = set(gold_ids)
    top_k = list(retrieved_ids[:k])
    hit = sum(1 for gid in gold if gid in top_k)
    return hit / len(gold)


def mrr(retrieved_ids: Sequence[str], gold_ids: Sequence[str]) -> float:
    """MRR：首个命中的倒数排名。空 gold / 无命中 → 0.0。

    多 gold 时取首个命中位置（与 TREC MRR 惯例一致），不是最优命中。
    """
    if not gold_ids:
        return 0.0
    gold = set(gold_ids)
    for i, rid in enumerate(retrieved_ids):
        if rid in gold:
            return 1.0 / (i + 1)
    return 0.0


def compute_all(retrieved_ids: Sequence[str], gold_ids: Sequence[str]) -> dict:
    """一键计算 hit@5 / hit@10 / recall@5 / recall@10 / mrr。"""
    return {
        "hit@5": hit_at_k(retrieved_ids, gold_ids, 5),
        "hit@10": hit_at_k(retrieved_ids, gold_ids, 10),
        "recall@5": recall_at_k(retrieved_ids, gold_ids, 5),
        "recall@10": recall_at_k(retrieved_ids, gold_ids, 10),
        "mrr": mrr(retrieved_ids, gold_ids),
    }
