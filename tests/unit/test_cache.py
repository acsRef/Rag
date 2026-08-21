"""app/core/cache.py 测试：EmbeddingCache + RetrievalCache（Day 1 上午）。"""

from app.core.cache import EmbeddingCache, RetrievalCache

# ── EmbeddingCache ──────────────────────────────────────────


def test_embedding_cache_set_and_get_round_trip():
    cache = EmbeddingCache()
    cache.set("hello", [0.1, 0.2, 0.3])
    assert cache.get("hello") == [0.1, 0.2, 0.3]


def test_embedding_cache_miss_returns_none():
    cache = EmbeddingCache()
    assert cache.get("nope") is None


def test_embedding_cache_distinguishes_similar_texts():
    """不同文本必须有不同 key——hash 碰撞测试。"""
    cache = EmbeddingCache()
    cache.set("foo", [1.0])
    cache.set("foo bar", [2.0])
    assert cache.get("foo") == [1.0]
    assert cache.get("foo bar") == [2.0]


def test_embedding_cache_overwrite_same_key():
    cache = EmbeddingCache()
    cache.set("x", [1.0])
    cache.set("x", [9.0, 9.0])
    assert cache.get("x") == [9.0, 9.0]


def test_embedding_cache_lru_evicts_oldest_on_overflow():
    cache = EmbeddingCache(max_size=2)
    cache.set("a", [1.0])
    cache.set("b", [2.0])
    cache.set("c", [3.0])  # 触发 LRU：淘汰 a
    assert cache.get("a") is None
    assert cache.get("b") == [2.0]
    assert cache.get("c") == [3.0]


def test_embedding_cache_get_refreshes_lru_order():
    cache = EmbeddingCache(max_size=2)
    cache.set("a", [1.0])
    cache.set("b", [2.0])
    _ = cache.get("a")  # 触碰 a → a 现在是 MRU，b 是 LRU
    cache.set("c", [3.0])  # 应当淘汰 b
    assert cache.get("a") == [1.0]
    assert cache.get("b") is None
    assert cache.get("c") == [3.0]


def test_embedding_cache_clear_resets():
    cache = EmbeddingCache()
    cache.set("a", [1.0])
    cache.set("b", [2.0])
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None
    assert len(cache) == 0


def test_embedding_cache_len_reflects_size():
    cache = EmbeddingCache()
    assert len(cache) == 0
    cache.set("a", [1.0])
    cache.set("b", [2.0])
    assert len(cache) == 2


# ── RetrievalCache ──────────────────────────────────────────


def test_retrieval_cache_set_and_get_round_trip():
    cache = RetrievalCache()
    items = [{"chunk_id": "c1", "score": 0.9}]
    cache.set("k1", items)
    assert cache.get("k1") == items


def test_retrieval_cache_miss_returns_none():
    cache = RetrievalCache()
    assert cache.get("absent") is None


def test_retrieval_cache_key_for_is_deterministic():
    """同输入必须得到同 key（哈希稳定）。"""
    k1 = RetrievalCache.key_for("q", {"a": 1}, 10, 2)
    k2 = RetrievalCache.key_for("q", {"a": 1}, 10, 2)
    assert k1 == k2


def test_retrieval_cache_key_for_filter_order_independent():
    """filter dict key 顺序不影响 key（json sort_keys）。"""
    k1 = RetrievalCache.key_for("q", {"a": 1, "b": 2}, 10, 2)
    k2 = RetrievalCache.key_for("q", {"b": 2, "a": 1}, 10, 2)
    assert k1 == k2


def test_retrieval_cache_key_for_changes_with_query():
    assert RetrievalCache.key_for("q1", None, 10, 2) != RetrievalCache.key_for("q2", None, 10, 2)


def test_retrieval_cache_key_for_changes_with_filters():
    k1 = RetrievalCache.key_for("q", {"years": [2024]}, 10, 2)
    k2 = RetrievalCache.key_for("q", {"years": [2023]}, 10, 2)
    assert k1 != k2


def test_retrieval_cache_key_for_changes_with_top_k():
    assert RetrievalCache.key_for("q", None, 10, 2) != RetrievalCache.key_for("q", None, 5, 2)


def test_retrieval_cache_key_for_changes_with_config_version():
    """config_version 必须参与 key——配置变更后旧 cache 必须失效。"""
    assert RetrievalCache.key_for("q", None, 10, 1) != RetrievalCache.key_for("q", None, 10, 2)


def test_retrieval_cache_key_for_none_filter_works():
    """filters=None 时 key 不报错。"""
    k = RetrievalCache.key_for("q", None, 10, 2)
    assert isinstance(k, str) and len(k) == 64  # sha256 hex


def test_retrieval_cache_lru_eviction():
    cache = RetrievalCache(max_size=2)
    cache.set("a", [{"x": 1}])
    cache.set("b", [{"x": 2}])
    cache.set("c", [{"x": 3}])
    assert cache.get("a") is None
    assert cache.get("b") == [{"x": 2}]
    assert cache.get("c") == [{"x": 3}]


def test_retrieval_cache_clear_resets():
    cache = RetrievalCache()
    cache.set("a", [{"x": 1}])
    cache.clear()
    assert cache.get("a") is None
    assert len(cache) == 0
