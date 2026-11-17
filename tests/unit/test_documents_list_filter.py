"""/api/v1/documents 列表的 `kb_id` 过滤（2026-11-17 修）。

背景：该端点此前**没有 kb_id 参数**，FastAPI 静默忽略未知 query → admin 调
`?kb_id=x` 拿到全库。真实事故：ReportAgent 的评测平台据此"清理临时知识库"，
把全库 14 份文档（1391 chunks）删光。

本测试离线（不碰 DB）：用假 session 记录 filter 调用，断言：
1. 给 kb_id 时确实对 `Document.kb_id` 加了条件；
2. 不给时**不加**该条件（保持原行为）；
3. 非 admin 的 owner 过滤依然叠加（权限不能被 kb_id 绕过）。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

import app.api.documents as mod
from app.api.documents import list_documents

ADMIN = {"id": "u-admin", "is_admin": True, "permissions": ["doc.read_all"]}
USER = {"id": "u-1", "is_admin": False, "permissions": []}


class _Doc:
    def __init__(self, document_id: str, kb_id: str, owner_id: str = "u-admin") -> None:
        self.document_id = document_id
        self.kb_id = kb_id
        self.owner_id = owner_id
        self.filename = f"{document_id}.md"
        self.status = "indexed"
        self.chunk_count = 1
        self.embedded_chunk_count = 1
        self.error_message = ""
        self.created_at = None


class _Query:
    def __init__(self, rows):
        self._rows = rows
        self.filters: list[str] = []

    def filter(self, expr):
        self.filters.append(str(expr))
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def offset(self, _n):
        return self

    def limit(self, _n):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._query = _Query(rows)

    def query(self, _model):
        return self._query


@pytest.fixture()
def fake_db(monkeypatch):
    rows = [_Doc("d-a", "kb-a"), _Doc("d-b", "kb-b")]

    @contextmanager
    def _ctx():
        yield _Session(rows)

    monkeypatch.setattr(mod, "get_db_ctx", _ctx)
    return rows


def test_kb_id_filter_is_built(fake_db, monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    @contextmanager
    def _ctx():
        session = _Session(fake_db)
        captured["filters"] = session._query.filters
        yield session

    monkeypatch.setattr(mod, "get_db_ctx", _ctx)
    list_documents(current_user=ADMIN, kb_id="kb-a", limit=50, offset=0)
    joined = " ".join(captured["filters"])
    # SQLAlchemy 会绑定参数：`documents.kb_id = :kb_id_1`
    assert "documents.kb_id" in joined or "kb_id" in joined
    assert ":kb_id" in joined, f"kb_id 应以绑定参数形式出现：{joined}"


def test_no_kb_id_keeps_old_behaviour(fake_db, monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    @contextmanager
    def _ctx():
        session = _Session(fake_db)
        captured["filters"] = session._query.filters
        yield session

    monkeypatch.setattr(mod, "get_db_ctx", _ctx)
    list_documents(current_user=ADMIN, kb_id=None, limit=50, offset=0)
    assert captured["filters"] == [], "不给 kb_id 时不应新增过滤条件（保持向后兼容）"


def test_non_admin_still_owner_filtered(fake_db, monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    @contextmanager
    def _ctx():
        session = _Session(fake_db)
        captured["filters"] = session._query.filters
        yield session

    monkeypatch.setattr(mod, "get_db_ctx", _ctx)
    list_documents(current_user=USER, kb_id="kb-a", limit=50, offset=0)
    joined = " ".join(captured["filters"])
    assert "kb_id" in joined and "owner_id" in joined, "kb_id 不能绕过 owner 过滤"
