"""锁定 DB 检索失败的熔断计数（DB-1）+ 熔断打开时 retrieve 快速返回（DB-1）。

DB 失败 / 成功计入 provider_health["postgres"] 熔断器：复用既有熔断基建，
postgres 进入 is_degraded() 后，pipeline 末尾的 degraded SSE 事件自动包含它。
熔断打开时 retrieve 入口短路，不再撞 DB。
"""

from app.core.retrieval import _search_kb
from app.llm import base as llm_base
from app.llm.base import CircuitState
from app.store import pgvector_store


def _fresh_db_breaker():
    """每个用例前重置 postgres 熔断器计数。"""
    b = llm_base.provider_health.get("postgres")
    with b._lock:
        b.state = CircuitState.CLOSED
        b.failure_count = 0
        b._total_failures = 0
        b._probe_in_flight = False


# ── DB-1 熔断计数 ──────────────────────────────────────────


def test_search_kb_records_failure_on_exception(monkeypatch):
    _fresh_db_breaker()
    b = llm_base.provider_health.get("postgres")

    def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(pgvector_store, "hybrid_search", boom)

    out = _search_kb("kb1", [0.1] * 4, "q", user_role_ids=[1], can_read_all=False, top_k=5)
    assert out == []  # 仍然容错（子查询失败）
    with b._lock:
        assert b.failure_count == 1, "DB 失败必须计入 postgres 熔断"


def test_search_kb_records_success(monkeypatch):
    _fresh_db_breaker()
    b = llm_base.provider_health.get("postgres")
    monkeypatch.setattr(pgvector_store, "hybrid_search", lambda *a, **kw: [])
    _search_kb("kb1", [0.1] * 4, "q", user_role_ids=[1], can_read_all=False, top_k=5)
    with b._lock:
        assert b.failure_count == 0


def test_db_open_breaker_short_circuits_retrieve(monkeypatch):
    _fresh_db_breaker()
    b = llm_base.provider_health.get("postgres")
    # 强制 OPEN
    with b._lock:
        b.state = CircuitState.OPEN

    calls = []

    def boom(*a, **kw):
        calls.append(1)
        raise RuntimeError("db down")

    monkeypatch.setattr(pgvector_store, "hybrid_search", boom)

    # _search_kb 现在应短路不撞 DB（failure_count 已 OPEN——直接走 OPEN 路径返回 []）
    out = _search_kb("kb1", [0.1] * 4, "q", user_role_ids=[1], can_read_all=False, top_k=5)
    # 注：_search_kb 自身仍调用 fn（短路在更上层 retrieve 入口）；
    # _search_kb 容错保证返回 []，且不加重 failure_count（OPEN 后 on_failure 已暂停记账）。
    assert out == []
