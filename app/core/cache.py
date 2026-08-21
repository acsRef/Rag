"""In-process LRU caches。

- `EmbeddingCache`：text → list[float]，sha256 key；用于 `SFEmbedding.embed` /
  `embed_single_chunk` 调用前去重。
- `RetrievalCache`：(query + filters + top_k + config_version) → list[SearchHit]，
  用 sha256 + JSON 序列化稳定 key。

设计要点：
- 进程内 OrderedDict LRU，不引入 Redis；ablation 后再决定是否升级
- key 与 value 都是可 JSON 化（vec 是纯 float list，hits 是 dict）
- settings 开关 `embedding_cache_enabled` / `retrieval_cache_enabled` 由调用方
  在使用前判读；cache 自身不带开关，方便单元测试
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any


class EmbeddingCache:
    """text → vec 的 LRU 缓存；满后淘汰最久未访问。"""

    def __init__(self, max_size: int = 4096):
        self.max_size = max_size
        self._store: OrderedDict[str, list[float]] = OrderedDict()

    @staticmethod
    def _key(text: str) -> str:
        # sha256 抗碰撞；utf-8 编码保证跨平台一致
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str) -> list[float] | None:
        """命中返回 vec；未命中返回 None。命中即把该 key 移到队尾（MRU）。"""
        k = self._key(text)
        if k in self._store:
            self._store.move_to_end(k)
            return self._store[k]
        return None

    def set(self, text: str, vec: list[float]) -> None:
        """写入 / 覆盖；超容时淘汰队首（LRU）。"""
        k = self._key(text)
        if k in self._store:
            self._store.move_to_end(k)
        self._store[k] = vec
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class RetrievalCache:
    """(query, filters, top_k, config_version) → retrieval hits 的 LRU 缓存。"""

    def __init__(self, max_size: int = 512):
        self.max_size = max_size
        self._store: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()

    @staticmethod
    def key_for(query: str, filters: dict | None, top_k: int, config_version: int) -> str:
        """稳定 key：filters 用 sort_keys JSON 序列化避免 dict 顺序干扰。

        config_version 由调用方提供（一般 = settings 的版本号或 hash）；改 retrieval
        流程后 bump 版本即可让旧 cache 自动失效。
        """
        f_str = json.dumps(filters or {}, sort_keys=True, ensure_ascii=False)
        raw = f"{query}\x00{f_str}\x00{top_k}\x00{config_version}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> list[dict[str, Any]] | None:
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        return None

    def set(self, key: str, items: list[dict[str, Any]]) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = items
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


# 模块级 singleton；settings.*_cache_enabled 控制是否实际使用
embedding_cache = EmbeddingCache()
retrieval_cache = RetrievalCache()
