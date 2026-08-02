"""锁定 app/core/mmr.py 行为：MMR 多样性重排与每文档软惩罚。

测试向量均取单位向量（dot = cosine），与 mmr 文档假设一致。
"""
import pytest

from app.core.mmr import mmr_select


def _cand(chunk_id: str, score: float, doc: str, emb: list) -> dict:
    return {"chunk_id": chunk_id, "score": score, "document_id": doc, "embedding": emb}


def test_empty_candidates_returns_empty():
    assert mmr_select([], top_k=5) == []


def test_lambda_one_is_pure_relevance():
    cands = [
        _cand("a", 0.9, "doc1", [1.0, 0.0, 0.0]),
        _cand("b", 0.8, "doc1", [1.0, 0.0, 0.0]),
        _cand("c", 0.7, "doc2", [0.0, 1.0, 0.0]),
    ]
    out = mmr_select(cands, lambda_=1.0, top_k=2, max_per_doc=99, doc_penalty=0.0)
    assert [c["chunk_id"] for c in out] == ["a", "b"]


def test_diversity_prefers_different_document():
    # b 与 a 语义完全相同（cosine=1）；c 来自另一文档且正交。
    # lambda=0.5 时，多样性项应让 c 胜出 b。
    # 数值推演：归一化分 a=1.0 b=0.5 c=0.0；
    #   b: 0.5*0.5 - 0.5*1.0 = -0.25
    #   c: 0.0     - 0.5*0.0 =  0.0   → c 胜
    cands = [
        _cand("a", 0.9, "doc1", [1.0, 0.0, 0.0]),
        _cand("b", 0.8, "doc1", [1.0, 0.0, 0.0]),
        _cand("c", 0.7, "doc2", [0.0, 1.0, 0.0]),
    ]
    out = mmr_select(cands, lambda_=0.5, top_k=2, max_per_doc=99, doc_penalty=0.0)
    assert [c["chunk_id"] for c in out] == ["a", "c"]


def test_max_per_doc_soft_penalty():
    # max_per_doc=1 + doc_penalty=0.5：
    # 归一化分 a=1.0 b=0.333 c=0.0；选 a 后：
    #   b: 0.333 - 0.5*(1-1+1) = -0.167
    #   c: 0.0   - 0           =  0.0   → c 胜
    cands = [
        _cand("a", 0.9, "doc1", [1.0, 0.0, 0.0]),
        _cand("b", 0.8, "doc1", [0.0, 0.0, 1.0]),
        _cand("c", 0.75, "doc2", [0.0, 1.0, 0.0]),
    ]
    out = mmr_select(cands, lambda_=1.0, top_k=2, max_per_doc=1, doc_penalty=0.5)
    assert [c["chunk_id"] for c in out] == ["a", "c"]


@pytest.mark.xfail(
    reason="已知 bug：chunk embedding 为 NULL 时 _embedding_to_list 返回 []，"
           "np.array 行长度不一致直接 ValueError；待 cross-doc-retrieval-overhaul plan 修复",
    strict=False,
)
def test_null_embedding_does_not_crash():
    cands = [
        _cand("a", 0.9, "doc1", [1.0, 0.0, 0.0]),
        {"chunk_id": "b", "score": 0.8, "document_id": "doc2", "embedding": None},
    ]
    out = mmr_select(cands, lambda_=0.7, top_k=2)
    assert len(out) == 2
