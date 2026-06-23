"""MMR (Maximal Marginal Relevance) diversity rerank with per-document soft constraint.

Designed to slot after cross-encoder rerank:
  cross-encoder → TopN → MMR(λ, max_per_doc) → TopK

Embedding vectors must be L2-normalized (cosine similarity = dot product).
"""

import numpy as np


def mmr_select(
    candidates: list[dict],
    lambda_: float = 0.7,
    top_k: int = 5,
    max_per_doc: int = 2,
    doc_penalty: float = 0.05,
) -> list[dict]:
    """Greedy MMR selection with per-document soft penalty.

    Parameters
    ----------
    candidates:
        Each dict must contain:
          - "score"       : float  — cross-encoder relevance score
          - "embedding"   : list[float] — L2-normalized embedding vector
          - "document_id" : str
    lambda_:
        Balance between relevance (1) and diversity (0).
        0.7 = 70% relevance, 30% diversity.
    top_k:
        Number of items to select.
    max_per_doc:
        Soft cap on items per document.  ≥ this triggers progressive penalty.
    doc_penalty:
        Penalty subtracted per extra item beyond max_per_doc.
        Must be tuned relative to normalised score range [0,1].

    Returns candidates in MMR selection order.
    """
    if not candidates or top_k <= 0:
        return candidates[:top_k]

    n = len(candidates)
    top_k = min(top_k, n)

    # 1. Min-max normalise cross-encoder scores to [0, 1]
    scores = [c["score"] for c in candidates]
    min_s, max_s = min(scores), max(scores)
    if max_s > min_s:
        norm_scores = [(s - min_s) / (max_s - min_s) for s in scores]
    else:
        norm_scores = [1.0] * n

    # Pre-extract document IDs and build embedding matrix (n, dim)
    doc_ids = [c["document_id"] for c in candidates]
    emb_matrix = np.array([c["embedding"] for c in candidates], dtype=np.float32)

    selected_indices: list[int] = []
    remaining = set(range(n))
    doc_count: dict[str, int] = {}

    for _ in range(top_k):
        best_idx = -1
        best_mmr = -np.inf

        for idx in remaining:
            # Relevance term
            relevance = lambda_ * norm_scores[idx]

            # Diversity term: max cosine similarity to any selected item
            if selected_indices:
                sims = emb_matrix[idx] @ emb_matrix[selected_indices].T
                max_sim = float(sims.max())
            else:
                max_sim = 0.0
            diversity = (1.0 - lambda_) * max_sim

            # Per-document soft penalty
            cnt = doc_count.get(doc_ids[idx], 0)
            penalty = 0.0
            if cnt >= max_per_doc:
                penalty = doc_penalty * (cnt - max_per_doc + 1)

            mmr = relevance - diversity - penalty
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = idx

        selected_indices.append(best_idx)
        remaining.remove(best_idx)
        d_id = doc_ids[best_idx]
        doc_count[d_id] = doc_count.get(d_id, 0) + 1

    return [candidates[i] for i in selected_indices]
