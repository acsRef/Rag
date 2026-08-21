"""设计审查 P0-3：删除文档/KB 必须清理 doc_relations 入边/出边。

delete_doc_relations_by_doc_id 之前从未被任何 API 路径调用，删除文档后
doc_relations 残留指向该 doc 的边，导致跨文档检索引用幽灵文档。
这里锁定两条删除路径都会清理 source+target 两侧。
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(integration_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _find_relations(doc_id: str) -> list:
    from app.store.db import DocRelation, get_db_ctx

    with get_db_ctx() as session:
        return (
            session.query(DocRelation)
            .filter((DocRelation.source_doc == doc_id) | (DocRelation.target_doc == doc_id))
            .all()
        )


def test_delete_document_cleans_relation_edges(client, admin_token, ingest_docs):
    """删除已建关系的文档后，doc_relations 无任何行指向它。"""
    from app.store import pgvector_store

    doc1 = ingest_docs["transformer_basics.md"]
    doc2 = ingest_docs["transformer_pytorch.md"]
    # 前提：摄入后确实存在 doc1↔doc2 的关系边
    rels = pgvector_store.get_doc_relations(doc1)
    assert any(r["target_doc"] == doc2 for r in rels), "前提失效：无关系边可删"

    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = client.delete(f"/api/v1/documents/{doc1}", headers=headers)
    assert resp.status_code == 200, resp.text

    assert _find_relations(doc1) == [], "删除文档后仍残留关系边"


def test_delete_kb_cleans_relation_edges(client, admin_token, integration_db):
    """删除 KB 时，其下文档的入边/出边一并清理。"""
    from app.store.db import (
        DocRelation,
        Document,
        KnowledgeBase,
        User,
        get_db_ctx,
        new_id,
        utc_now,
    )

    # admin 用户 id 是 UUID（seed_defaults 生成），需查询真实 id 满足 FK
    with get_db_ctx() as session:
        admin_id = session.query(User).filter(User.username == "admin").first().id

    with get_db_ctx() as session:
        kb_id = new_id()
        session.add(
            KnowledgeBase(
                id=kb_id,
                name="删除测试 KB",
                visibility="public",
                owner_id=admin_id,
            )
        )
        session.flush()
        doc_id = new_id()
        other1 = new_id()
        other2 = new_id()
        session.add(
            Document(
                document_id=doc_id,
                kb_id=kb_id,
                filename="del.md",
                owner_id=admin_id,
                status="indexed",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        # 邻居文档须真实存在（doc_relations 两侧都有 FK 指向 documents）
        for oid in (other1, other2):
            session.add(
                Document(
                    document_id=oid,
                    kb_id=kb_id,
                    filename="neighbor.md",
                    owner_id=admin_id,
                    status="indexed",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
        session.flush()
        # 双向边 + 第三方边，全部应随 KB 删除清空
        session.add(
            DocRelation(
                source_doc=doc_id,
                target_doc=other1,
                cosine=0.5,
                entity_jaccard=0.1,
                relation_type="tfidf",
            )
        )
        session.add(
            DocRelation(
                source_doc=other2,
                target_doc=doc_id,
                cosine=0.5,
                entity_jaccard=0.1,
                relation_type="tfidf",
            )
        )
        session.commit()
        kb_under_test = kb_id
        doc_under_test = doc_id

    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = client.delete(f"/api/v1/kb/{kb_under_test}", headers=headers)
    assert resp.status_code == 200, resp.text

    assert _find_relations(doc_under_test) == [], "删除 KB 后仍残留关系边"
