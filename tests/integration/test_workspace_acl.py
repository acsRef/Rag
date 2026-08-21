"""工作空间隐私：注册默认 restricted + 检索 owner 旁路 ACL。

旧实现注册创建的工作空间 visibility="public"，且 chunk ACL 无 owner 概念
——个人文档上传后全员可检索。修复：默认 restricted + 检索 SQL 增加
「文档属主」旁路（admin/can_read_all 不受影响）。
"""

import asyncio

import pytest
from sqlalchemy import text as sqlt


@pytest.fixture(scope="module")
def client(integration_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_register_workspace_kb_defaults_to_restricted(client, integration_db):
    import uuid

    # 限流桶是进程级共享状态：test_security_api 的限流用例会把它打满，
    # 此处先清空，避免跨用例串扰
    import app.api.auth as auth_mod

    auth_mod._LOGIN_ATTEMPTS.clear()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "username": "ws_user_%s" % uuid.uuid4().hex[:10],
            "password": "password123",
        },
    )
    assert resp.status_code == 200, resp.text
    kb_id = resp.json()["user"]["workspace_kb_id"]
    assert kb_id
    from app.store.db import KnowledgeBase, get_db_ctx

    with get_db_ctx() as session:
        kb = session.query(KnowledgeBase).filter_by(id=kb_id).first()
    assert kb is not None
    assert kb.visibility == "restricted", "个人工作空间不得默认公开"


async def test_restricted_doc_visible_to_owner_only(integration_db, fake_llm_stack):
    """restricted 文档：属主可检索、他人不可、admin 可。"""
    from app.core.retrieval import retrieval_engine
    from app.ingestion.indexer import document_indexer
    from app.store.db import (
        Document,
        KnowledgeBase,
        User,
        get_db_ctx,
        new_id,
        utc_now,
    )

    with get_db_ctx() as session:
        session.execute(
            sqlt(
                "TRUNCATE chunks, chunk_questions, documents, doc_entities, "
                "doc_relations, doc_embeddings, doc_role_access RESTART IDENTITY"
            )
        )
        for uid in ("alice", "bob"):
            if not session.query(User).filter_by(id=uid).first():
                session.add(User(id=uid, username=uid, hashed_password="unused", is_active=True))
        session.flush()
        if not session.query(KnowledgeBase).filter_by(id="alice-ws").first():
            session.add(
                KnowledgeBase(
                    id="alice-ws",
                    name="Alice 的工作空间",
                    visibility="restricted",
                    owner_id="alice",
                )
            )
        session.commit()

    # 与 api/documents.py 上传路径一致：Document 行先行，restricted + 空角色名单
    doc_id = new_id()
    with get_db_ctx() as session:
        session.add(
            Document(
                document_id=doc_id,
                kb_id="alice-ws",
                filename="private.md",
                owner_id="alice",
                status="processing",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()
    content = "# 私密文档\n\n### 唯一小节\n\n这是爱丽丝的私密文档，记载青龙计划的机密预算数据。\n"
    # 与生产一致：indexer 经 asyncio.to_thread 在工作线程执行（内部 asyncio.run
    # 依赖「无运行中事件循环」）
    res = await asyncio.to_thread(
        document_indexer.index,
        "private.md",
        content.encode("utf-8"),
        kb_id="alice-ws",
        user_id="alice",
        visibility="restricted",
        allowed_roles=[],
        document_id=doc_id,
    )
    assert res["status"] == "indexed", res

    async def _retrieve(uid: str, can_read_all: bool = False):
        return await retrieval_engine.retrieve(
            "青龙计划 机密预算", None, user_role_ids=[], can_read_all=can_read_all, user_id=uid
        )

    alice_hits = await _retrieve("alice")
    assert any(c.document_id == doc_id for c in alice_hits), (
        "属主搜不到自己的 restricted 文档（owner 旁路缺失）"
    )

    bob_hits = await _retrieve("bob")
    assert not any(c.document_id == doc_id for c in bob_hits), (
        "非属主检索到了他人的 restricted 文档"
    )

    admin_hits = await _retrieve("", can_read_all=True)
    assert any(c.document_id == doc_id for c in admin_hits), "can_read_all 管理员应可见"


@pytest.fixture(scope="module")
def admin_token(client):
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_diag_detail_rejects_path_traversal(client, admin_token):
    """/diag/detail/{id:path} 允许斜杠——非预期字符集必须 404，不得读目录外文件。"""
    headers = {"Authorization": f"Bearer {admin_token}"}
    for evil in ("../../index", "..%2F..%2Findex", "a/b", "..", "x" * 200):
        resp = client.get(f"/api/v1/diag/detail/{evil}", headers=headers)
        assert resp.status_code == 404, f"路径 {evil!r} 未被拦截: {resp.status_code}"


def test_diag_chunk_doc_rejects_traversal_id(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = client.get("/api/v1/diag/chunk-doc/..%2e.secret", headers=headers)
    assert resp.status_code == 404
