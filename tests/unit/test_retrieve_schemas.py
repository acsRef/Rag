"""Retrieve 端点请求/响应模型的契约测试。"""

import pytest
from pydantic import ValidationError

from app.models.schemas import RetrievedItem, RetrieveRequest, RetrieveResponse


def test_retrieve_request_defaults():
    body = RetrieveRequest(query="销售额", kb_ids=["kb-1"])
    assert body.top_k == 5


def test_retrieve_request_rejects_empty_kb_ids():
    with pytest.raises(ValidationError):
        RetrieveRequest(query="销售额", kb_ids=[])


def test_retrieve_request_kb_ids_max_length():
    # 上限 20：21 个拒绝，20 个边界放行
    with pytest.raises(ValidationError):
        RetrieveRequest(query="q", kb_ids=[f"kb-{i}" for i in range(21)])
    assert len(RetrieveRequest(query="q", kb_ids=[f"kb-{i}" for i in range(20)]).kb_ids) == 20


def test_retrieve_request_rejects_bad_query():
    with pytest.raises(ValidationError):
        RetrieveRequest(query="", kb_ids=["kb-1"])
    with pytest.raises(ValidationError):
        RetrieveRequest(query="x" * 4097, kb_ids=["kb-1"])


def test_retrieve_request_top_k_bounds():
    with pytest.raises(ValidationError):
        RetrieveRequest(query="q", kb_ids=["k"], top_k=0)
    with pytest.raises(ValidationError):
        RetrieveRequest(query="q", kb_ids=["k"], top_k=51)


def test_retrieve_request_top_k_valid_bounds():
    assert RetrieveRequest(query="q", kb_ids=["k"], top_k=1).top_k == 1
    assert RetrieveRequest(query="q", kb_ids=["k"], top_k=50).top_k == 50


def test_retrieved_item_required_fields():
    with pytest.raises(ValidationError):
        RetrievedItem(chunk_id="c1", text="正文", score=0.5)  # 缺 document_id
    with pytest.raises(ValidationError):
        RetrievedItem(chunk_id="c1", document_id="d1", text="正文")  # 缺 score


def test_retrieve_response_defaults():
    resp = RetrieveResponse(items=[])
    assert resp.degraded is False


def test_retrieve_response_shape():
    item = RetrievedItem(
        chunk_id="c1", document_id="d1", text="正文", title="t", section_path="s", score=0.5
    )
    resp = RetrieveResponse(items=[item], degraded=False)
    assert resp.items[0].score == 0.5
