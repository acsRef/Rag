"""integration 测试底座：ragent_test 测试库 + 确定性 fake LLM/embedding/rerank 层。

PostgreSQL 不可达时全部 integration 用例 skip，离线环境跑全量套件不受影响。
"""
import hashlib
import math
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config import settings

ADMIN_URL = "postgresql://ragent:ragent@localhost:5432/postgres"
TEST_DB_URL = "postgresql://ragent:ragent@localhost:5432/ragent_test"
FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "docs"


def _probe_pg() -> bool:
    try:
        eng = create_engine(ADMIN_URL, connect_args={"connect_timeout": 3})
        with eng.connect() as conn:
            conn.execute(text("select 1"))
        eng.dispose()
        return True
    except Exception:
        return False


PG_AVAILABLE = _probe_pg()

if PG_AVAILABLE:
    # 确保测试库存在（含 pgvector 扩展），并把 app 的 engine 指向测试库。
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'ragent_test'")
        ).scalar():
            conn.execute(text("CREATE DATABASE ragent_test"))
    admin.dispose()
    boot = create_engine(TEST_DB_URL)
    with boot.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    boot.dispose()
    os.environ["DATABASE_URL"] = TEST_DB_URL
    settings.database_url = TEST_DB_URL   # app.store.db 在此之后才会被 import


@pytest.fixture(scope="session")
def integration_db():
    """建表 + 清理数据表 + seed 测试用户/知识库。PG 不可达则 skip。"""
    if not PG_AVAILABLE:
        pytest.skip("PostgreSQL (localhost:5432) 不可达，integration 测试跳过")

    from app.store import db as db_mod
    from app.core.pii_rules import seed_pii_rules

    db_mod.init_db()
    # 与 main.py 启动序列对齐：没有 seed 的规则表 = PII 检测空转
    seed_pii_rules()

    with db_mod.get_db_ctx() as session:
        # 注意 1：不能带 CASCADE——documents/messages 外键引用 users/knowledge_bases，
        # CASCADE 会把 seed 目标表一并清空。表清单本身已是引用闭包。
        # 注意 2：不要清 user_roles——seed_defaults 按"用户是否存在"跳过重建，
        # 清掉后跨会话留存的 admin 用户会永久失去角色关联（is_admin=False）。
        session.execute(text(
            "TRUNCATE chunks, chunk_questions, documents, doc_entities, "
            "doc_relations, doc_embeddings, conversations, messages, "
            "pii_alerts, pii_hold, doc_role_access, kb_role_access "
            "RESTART IDENTITY"
        ))
        session.execute(text("DELETE FROM knowledge_bases WHERE id = 'test-kb'"))
        session.execute(text("DELETE FROM users WHERE id = 'test-user'"))
        # 模型间没有 relationship() 声明，flush 顺序不保证——
        # 必须先 flush user 再插 KB，否则 KB 的 INSERT 可能抢跑触发 FK 违反。
        session.add(db_mod.User(
            id="test-user", username="test-user",
            hashed_password="unused-in-tests", is_active=True,
        ))
        session.flush()
        session.add(db_mod.KnowledgeBase(
            id="test-kb", name="测试知识库", visibility="public", owner_id="test-user",
        ))
        session.commit()

    yield db_mod


# ── 确定性 fake 层 ──────────────────────────────────────

_DIM = settings.embedding_dimension
_TOKEN_RE = re.compile(r"[一-鿿]|[A-Za-z0-9]+")


def fake_vector(text_input: str) -> list:
    """md5 哈希词袋 → 4096 维 L2 归一化向量。共享词的文本余弦高，确定且跨进程稳定。"""
    v = [0.0] * _DIM
    for tok in _TOKEN_RE.findall(text_input.lower()):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        v[h % _DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm else v


@pytest.fixture
def fake_llm_stack(monkeypatch):
    """替换 embedding / rerank / metadata 三个外部依赖为确定性离线实现。"""
    from app.llm.embedding import sf_embedding
    from app.llm.rerank import sf_rerank
    from app.ingestion.metadata import chunk_metadata_generator

    calls = {"embed_with_fallback": []}

    async def fake_embed(query, **kw):
        return fake_vector(query)

    async def fake_embed_batch(texts, **kw):
        calls["embed_with_fallback"].append(list(texts))
        return [(fake_vector(t), None) for t in texts]

    async def fake_rerank(query, texts, **kw):
        # 恒等排序的伪分数：极差 > 0.001，让 retrieval 的"无区分度跳过"分支不触发
        return [
            {"index": i, "relevance_score": 1.0 - i * 0.01}
            for i in range(len(texts))
        ]

    def fake_generate(chunks):
        for i, c in enumerate(chunks):
            head = c.text[:20].replace("\n", " ")
            c.title = "标题-%d-%s" % (i, head)
            c.summary = "摘要：%s" % head
            c.questions = ["%s是什么？" % head, "如何理解%s？" % head]
        return chunks

    monkeypatch.setattr(sf_embedding, "embed", fake_embed)
    monkeypatch.setattr(sf_embedding, "embed_with_fallback", fake_embed_batch)
    monkeypatch.setattr(sf_rerank, "rerank", fake_rerank)
    monkeypatch.setattr(chunk_metadata_generator, "generate", fake_generate)
    return calls


@pytest.fixture
def ingest_docs(integration_db, fake_llm_stack):
    """摄入三份 fixture 文档，返回 {filename: document_id}。

    先预建 Document 行再 index——与 api/documents.py 的上传契约一致：
    indexer 的 add_chunks 先于 _save_document 执行，FK schema 下
    没有预建行的 index(document_id=None) 会直接 FK 违反。

    每次调用先清空语料相关表：函数级 fixture 会多代累积同文本文档，
    旧代副本与当代词法/向量不可区分，会污染跨文档测试的确定性。
    """
    from app.ingestion.indexer import document_indexer
    from app.store.db import get_db_ctx, Document, new_id, utc_now

    with get_db_ctx() as session:
        session.execute(text(
            "TRUNCATE chunks, chunk_questions, documents, doc_entities, "
            "doc_relations, doc_embeddings, doc_role_access RESTART IDENTITY"
        ))
        session.commit()

    ids = {}
    for name in ("transformer_basics.md", "transformer_pytorch.md", "rag_chunking.md"):
        doc_id = new_id()
        with get_db_ctx() as session:
            session.add(Document(
                document_id=doc_id, kb_id="test-kb", filename=name,
                owner_id="test-user", status="processing",
                created_at=utc_now(), updated_at=utc_now(),
            ))
            session.commit()
        res = document_indexer.index(
            name, (FIXTURE_DIR / name).read_bytes(),
            kb_id="test-kb", user_id="test-user", document_id=doc_id,
        )
        assert res["status"] == "indexed", "摄入 %s 失败: %s" % (name, res)
        ids[name] = doc_id
    return ids


@pytest.fixture
def live_env(monkeypatch):
    """真实 API 环境：RAGENT_LIVE_LLM=1 且 .env 有真实 key 才放行。

    供所有 live_llm 集成测试共享（key 注入 + 强制按新 key 重建 client）。
    """
    import os as _os
    if _os.environ.get("RAGENT_LIVE_LLM") != "1":
        pytest.skip("未设置 RAGENT_LIVE_LLM=1，跳过真实 API 测试")
    from dotenv import dotenv_values
    vals = dotenv_values(".env")
    mm_key = (vals.get("MINIMAX_API_KEY") or "").strip()
    sf_key = (vals.get("SILICONFLOW_API_KEY") or "").strip()
    if not mm_key or not sf_key:
        pytest.skip(".env 缺少 MINIMAX_API_KEY / SILICONFLOW_API_KEY")
    monkeypatch.setattr(settings, "minimax_api_key", mm_key)
    monkeypatch.setattr(settings, "siliconflow_api_key", sf_key)
    from app.llm.chat import minimax_client
    from app.llm.embedding import sf_embedding
    for client in (minimax_client, sf_embedding):
        monkeypatch.setattr(client, "_client", None, raising=False)
        monkeypatch.setattr(client, "_client_loop_id", None, raising=False)
    yield


@pytest.fixture
def clean_corpus(integration_db):
    """清空语料相关表：live 模块间共享 session，语料会跨模块累积，
    污染 top-k 检索断言；每个 live 模块摄入前先清场。"""
    with integration_db.get_db_ctx() as session:
        session.execute(text(
            "TRUNCATE chunks, chunk_questions, documents, doc_entities, "
            "doc_relations, doc_embeddings, doc_role_access RESTART IDENTITY"))
        session.commit()
    yield
