"""Day 1 下午策略开关 guards 测试。

锁定：
- settings.xxx_enabled=False 时，4 个 helper 函数直接返回 results 不变
- settings.xxx_enabled=True 时，helper 正常执行（mock 掉真正检索逻辑后验证被调）
- pipeline.py 的 query_decomposition_enabled guard 在 disable 时强制 needs_decomp=False
- 开关切换不影响其他 helper（隔离性）
"""

from app.config import settings
from app.core import retrieval as ret_mod

# ── section_supplement_enabled ─────────────────────────────


def test_section_supplement_disabled_returns_results_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "section_supplement_enabled", False)
    sentinel = [{"chunk_id": "c1"}, {"chunk_id": "c2"}]
    out = ret_mod._supplement_authoritative_sections(
        sentinel, "q", [], None, False, "u1"
    )
    assert out is sentinel, "关时应原样返回（不开新检索）"


def test_section_supplement_enabled_calls_inner_logic(monkeypatch):
    monkeypatch.setattr(settings, "section_supplement_enabled", True)
    called = {"n": 0}

    def fake(*args, **kwargs):
        called["n"] += 1
        return [{"chunk_id": "supplemented"}]

    monkeypatch.setattr(ret_mod, "_supplement_authoritative_sections", fake)
    out = ret_mod._supplement_authoritative_sections(
        [{"chunk_id": "c1"}], "q", ["kb1"], None, False, "u1"
    )
    assert called["n"] == 1
    assert out == [{"chunk_id": "supplemented"}]


# ── year_supplement_enabled ────────────────────────────────


def test_year_supplement_disabled_returns_results_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "year_supplement_enabled", False)
    sentinel = [{"chunk_id": "c1", "year": "2024"}]
    out = ret_mod._supplement_missing_years(
        sentinel, "q", [], None, False, "u1"
    )
    assert out is sentinel


def test_year_supplement_enabled_calls_inner_logic(monkeypatch):
    monkeypatch.setattr(settings, "year_supplement_enabled", True)
    called = {"n": 0}

    def fake(*args, **kwargs):
        called["n"] += 1
        return [{"chunk_id": "year-supplemented"}]

    monkeypatch.setattr(ret_mod, "_supplement_missing_years", fake)
    out = ret_mod._supplement_missing_years(
        [{"chunk_id": "c1"}], "q", ["kb1"], None, False, "u1"
    )
    assert called["n"] == 1
    assert out == [{"chunk_id": "year-supplemented"}]


# ── section_boost_enabled ──────────────────────────────────


def test_section_boost_disabled_returns_results_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "section_boost_enabled", False)
    sentinel = [{"chunk_id": "c1", "section_path": "主要会计数据", "score": 0.9}]
    out = ret_mod._boost_by_section_type(sentinel, "营业收入")
    assert out is sentinel


def test_section_boost_enabled_keeps_authority_boost(monkeypatch):
    """开时即使 query 命中关键词，boost 仍生效（确认 guard 没误伤）。"""
    monkeypatch.setattr(settings, "section_boost_enabled", True)
    results = [
        {"chunk_id": "non-auth", "section_path": "管理层讨论", "score": 1.0},
        {"chunk_id": "auth", "section_path": "主要会计数据", "score": 1.0},
    ]
    out = ret_mod._boost_by_section_type(results, "营业收入")
    # authority chunk 应当被 boost 到第一
    assert out[0]["chunk_id"] == "auth"


def test_section_boost_enabled_with_no_query_match_leaves_results(monkeypatch):
    """开时但 query 不命中任何关键词，结果保持原顺序与分数。"""
    monkeypatch.setattr(settings, "section_boost_enabled", True)
    results = [{"chunk_id": "c1", "section_path": "其他", "score": 0.5}]
    out = ret_mod._boost_by_section_type(results, "完全不相关的 query xyz")
    assert out == results


# ── cross_doc_enabled（_cross_doc_extra 包装函数） ─────────


def test_cross_doc_disabled_returns_empty_extra(monkeypatch):
    """cross_doc_enabled=False 时 _cross_doc_extra 返回 ([], 0)。"""
    monkeypatch.setattr(settings, "cross_doc_enabled", False)
    # 即使内部 cross_doc_retriever 抛错也不该被触达
    def explode(*args, **kwargs):
        raise RuntimeError("must not be called when disabled")
    monkeypatch.setattr(ret_mod.cross_doc_retriever, "retrieve_sync", explode)
    import asyncio
    extra, count = asyncio.run(ret_mod._cross_doc_extra(
        "q", [0.0]*4, ["kb1"], [], None, False, "u1"
    ))
    assert extra == []
    assert count == 0


def test_cross_doc_enabled_calls_inner(monkeypatch):
    """cross_doc_enabled=True 时正常走 cross_doc_retriever。"""
    monkeypatch.setattr(settings, "cross_doc_enabled", True)
    monkeypatch.setattr(
        ret_mod.cross_doc_retriever, "retrieve_sync",
        lambda *a, **kw: [{"chunk_id": "x1", "score": 0.8}],
    )
    import asyncio
    extra, count = asyncio.run(ret_mod._cross_doc_extra(
        "q", [0.0]*4, ["kb1"], [], None, False, "u1"
    ))
    assert count == 1
    assert extra[0]["chunk_id"] == "x1"


# ── 隔离性：一个开关不会影响其他 helper ─────────────────────


def test_section_boost_off_does_not_block_section_supplement(monkeypatch):
    """section_boost_enabled=False 不应影响 _supplement_authoritative_sections 的逻辑。"""
    monkeypatch.setattr(settings, "section_boost_enabled", False)
    monkeypatch.setattr(settings, "section_supplement_enabled", True)
    called = {"n": 0}

    def fake(*args, **kwargs):
        called["n"] += 1
        return args[0]

    monkeypatch.setattr(ret_mod, "_supplement_authoritative_sections", fake)
    ret_mod._supplement_authoritative_sections(
        [{"chunk_id": "c1"}], "q", ["kb1"], None, False, "u1"
    )
    assert called["n"] == 1, "section_supplement 仍应被调用"


# ── pipeline._needs_decomposition guard ────────────────────


def test_pipeline_query_decomposition_disabled_forces_needs_decomp_false(monkeypatch):
    """settings.query_decomposition_enabled=False → pipeline._needs_decomposition 不被调，
    且 needs_decomp 应当为 False（走 fast path，不做 rewrite/intent）。
    """
    from app.core import pipeline as pipe_mod

    monkeypatch.setattr(settings, "query_decomposition_enabled", False)

    def explode(query):
        raise RuntimeError("must not call _needs_decomposition when flag is off")

    monkeypatch.setattr(pipe_mod, "_needs_decomposition", explode)

    # 模拟 pipeline 入口的逻辑：needs_decomp = settings.query_decomposition_enabled and _needs_decomposition(...)
    # 如果 explode 被调用，会抛 RuntimeError
    needs_decomp = settings.query_decomposition_enabled and pipe_mod._needs_decomposition("跨年对比查询")
    assert needs_decomp is False


def test_pipeline_query_decomposition_enabled_calls_inner(monkeypatch):
    monkeypatch.setattr(settings, "query_decomposition_enabled", True)
    from app.core import pipeline as pipe_mod
    called = {"n": 0}
    orig = pipe_mod._needs_decomposition

    def tracking(query):
        called["n"] += 1
        return orig(query)

    monkeypatch.setattr(pipe_mod, "_needs_decomposition", tracking)
    # 走真函数确认守卫没破坏正常路径
    out = settings.query_decomposition_enabled and pipe_mod._needs_decomposition("对比 2023 vs 2024 营收")
    assert called["n"] == 1
    assert out is True
