"""/api/v1/retrieve：鉴权（visibility + read_all bypass）、kb_ids 隔离、降级标志、空结果。

离线：dependency_overrides 提供假用户；hybrid_search / KB 可读性判定 monkeypatch，
不触真实 DB 与 embedding 服务。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.retrieve import router
from app.middleware.auth import get_current_user


ADMIN = {"id": "u-admin", "is_admin": True, "permissions": ["doc.read_all"], "role_ids": []}
USER = {"id": "u-1", "is_admin": False, "permissions": [], "role_ids": [2]}


@pytest.fixture
def client(request):
    user = getattr(request, "param", ADMIN)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_layers(monkeypatch):
    import app.api.retrieve as mod
    monkeypatch.setattr(mod, "_assert_kb_readable", lambda session, user, kb_ids: None)

    async def _fake_embed(query, ctx=None):
        return [0.0] * 4, False
    monkeypatch.setattr(mod, "embed_query_with_fallback", _fake_embed)

    calls = {}

    def _fake_hybrid(**kwargs):
        calls.update(kwargs)
        return [{"chunk_id": "c1", "document_id": "d1", "text": "字典正文",
                 "title": "dict-table_public_fact_sales.md", "section_path": "字段", "score": 0.9}]
    monkeypatch.setattr(mod, "hybrid_search", _fake_hybrid)
    return calls


def test_retrieve_happy_path(client, _stub_layers):
    resp = client.post("/api/v1/retrieve", json={"query": "销售额", "kb_ids": ["kb-dict"], "top_k": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is False
    assert body["items"][0]["document_id"] == "d1"
    # kb_ids 必须原样 pin 进 hybrid_search（隔离保证）
    assert _stub_layers["kb_ids"] == ["kb-dict"]
    assert _stub_layers["top_k"] == 3


@pytest.mark.parametrize("client", [USER], indirect=True)
def test_retrieve_forbidden_kb(client, monkeypatch, _stub_layers):
    import app.api.retrieve as mod
    from fastapi import HTTPException

    def _deny(session, user, kb_ids):
        raise HTTPException(status_code=403, detail=f"无权读取知识库: {kb_ids[0]}")
    monkeypatch.setattr(mod, "_assert_kb_readable", _deny)
    resp = client.post("/api/v1/retrieve", json={"query": "q", "kb_ids": ["kb-x"]})
    assert resp.status_code == 403


def test_retrieve_degraded_flag(client, monkeypatch, _stub_layers):
    import app.api.retrieve as mod

    async def _degraded(query, ctx=None):
        return [0.0] * 4, True
    monkeypatch.setattr(mod, "embed_query_with_fallback", _degraded)
    resp = client.post("/api/v1/retrieve", json={"query": "q", "kb_ids": ["kb-dict"]})
    assert resp.status_code == 200
    assert resp.json()["degraded"] is True


def test_retrieve_pure_vector_none_returns_empty(client, monkeypatch, _stub_layers):
    import app.api.retrieve as mod

    async def _none(query, ctx=None):
        return None, True
    monkeypatch.setattr(mod, "embed_query_with_fallback", _none)
    resp = client.post("/api/v1/retrieve", json={"query": "q", "kb_ids": ["kb-dict"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["degraded"] is True


def test_retrieve_empty_result_semantics(client, monkeypatch, _stub_layers):
    import app.api.retrieve as mod
    monkeypatch.setattr(mod, "hybrid_search", lambda **kw: [])
    resp = client.post("/api/v1/retrieve", json={"query": "不存在的字段", "kb_ids": ["kb-dict"]})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ── _assert_kb_readable 真实分支矩阵（离线 SQLite 内存库，不触 PG） ──

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.retrieve import _assert_kb_readable
from app.store.db import Base, KBRoleAccess, KnowledgeBase


def _readability_session():
    """内存 SQLite：只建 KnowledgeBase / KBRoleAccess 两张普通表，
    预置 public / internal / restricted 各一个 KB + 一条 role_id=2 的授权。"""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[KnowledgeBase.__table__, KBRoleAccess.__table__])
    session = sessionmaker(bind=engine)()
    session.add(KnowledgeBase(id="kb-pub", name="pub", visibility="public", owner_id="u-other"))
    session.add(KnowledgeBase(id="kb-int", name="int", visibility="internal", owner_id="u-other"))
    session.add(KnowledgeBase(id="kb-res", name="res", visibility="restricted", owner_id="u-other"))
    session.add(KnowledgeBase(id="kb-mine", name="mine", visibility="internal", owner_id="u-1"))
    session.add(KBRoleAccess(kb_id="kb-int", role_id=2))      # internal 对 role 2 放行
    session.add(KBRoleAccess(kb_id="kb-mine", role_id=9))     # 干扰项：owner 判定不应依赖角色
    session.commit()
    return session


_PLAIN = {"id": "u-1", "is_admin": False, "permissions": [], "role_ids": [2]}


def test_readability_admin_and_read_all_bypass():
    session = _readability_session()
    admin = {"id": "u-admin", "is_admin": True, "permissions": [], "role_ids": []}
    auditor = {"id": "u-aud", "is_admin": False, "permissions": ["doc.read_all"], "role_ids": []}
    # bypass 对任意 kb_id（含 restricted / 不存在）都不查库、不抛错
    _assert_kb_readable(session, admin, ["kb-res", "kb-nope"])
    _assert_kb_readable(session, auditor, ["kb-res"])


def test_readability_public_and_owner_allowed():
    session = _readability_session()
    _assert_kb_readable(session, _PLAIN, ["kb-pub"])
    _assert_kb_readable(session, _PLAIN, ["kb-mine"])


def test_readability_role_hit_allowed():
    session = _readability_session()
    _assert_kb_readable(session, _PLAIN, ["kb-int"])  # internal + role 2 命中 KBRoleAccess


def test_readability_restricted_without_role_denied():
    session = _readability_session()
    with pytest.raises(HTTPException) as exc:
        _assert_kb_readable(session, _PLAIN, ["kb-res"])  # restricted 未授权 role 2
    assert exc.value.status_code == 403
    assert "kb-res" in exc.value.detail


def test_readability_internal_no_roles_denied():
    session = _readability_session()
    no_roles = {"id": "u-2", "is_admin": False, "permissions": [], "role_ids": []}
    with pytest.raises(HTTPException) as exc:
        _assert_kb_readable(session, no_roles, ["kb-int"])
    assert exc.value.status_code == 403


def test_readability_missing_kb_denied_without_leaking_existence():
    session = _readability_session()
    with pytest.raises(HTTPException) as exc:
        _assert_kb_readable(session, _PLAIN, ["kb-ghost"])
    assert exc.value.status_code == 403
    assert "知识库不存在或不可读" in exc.value.detail


def test_readability_multi_kb_any_denied_blocks_all():
    session = _readability_session()
    with pytest.raises(HTTPException) as exc:
        _assert_kb_readable(session, _PLAIN, ["kb-pub", "kb-res"])  # 任一越权即整体拒绝
    assert exc.value.status_code == 403
    assert "kb-res" in exc.value.detail
