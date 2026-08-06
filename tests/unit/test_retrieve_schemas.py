"""Retrieve 端点请求/响应模型的契约测试。"""
import pytest
from pydantic import ValidationError


def test_retrieve_request_defaults():
    from app.models.schemas import RetrieveRequest
    body = RetrieveRequest(query="销售额", kb_ids=["kb-1"])
    assert body.top_k == 5


def test_retrieve_request_rejects_empty_kb_ids():
    from app.models.schemas import RetrieveRequest
    with pytest.raises(ValidationError):
        RetrieveRequest(query="销售额", kb_ids=[])


def test_retrieve_request_top_k_bounds():
    from app.models.schemas import RetrieveRequest
    with pytest.raises(ValidationError):
        RetrieveRequest(query="q", kb_ids=["k"], top_k=0)
    with pytest.raises(ValidationError):
        RetrieveRequest(query="q", kb_ids=["k"], top_k=51)


def test_retrieve_response_shape():
    from app.models.schemas import RetrieveResponse, RetrievedItem
    item = RetrievedItem(chunk_id="c1", document_id="d1", text="正文",
                         title="t", section_path="s", score=0.5)
    resp = RetrieveResponse(items=[item], degraded=False)
    assert resp.items[0].score == 0.5
