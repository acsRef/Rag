"""检索层过滤器（Day 1 下午）。

所有"散装过滤"统一收口：
- 检索层 / pipeline 内部不再用裸 list[document_ids] / if year == ... 这类零散判断
- hybrid_search 接收 RetrievalFilter，统一翻译成 SQL WHERE
- frozen=True 强调"查询意图快照，构造后不可改"——可作为 cache key、可哈希
- 全部 Optional + None：未设 = 不限（None 语义与空集合语义不同：None 不构造任何限制）

对应列尚未全部就绪：
- year / table_title / figure_title 列在 Day 2 上午随 chunk 字段一起加
- 本 dataclass 先稳定契约；hybrid_search 当下只翻译 document_ids 与 kb_ids
  字段（这俩已经能命中 SQL）；years/section_names/source_types 留好接口待 Day 2 接通
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def _to_frozenset(value: Iterable | None) -> frozenset | None:
    """list/set/tuple/None → frozenset | None；None 保持 None（语义不同于空集合）。"""
    if value is None:
        return None
    if isinstance(value, frozenset):
        return value
    return frozenset(value)


@dataclass(frozen=True)
class RetrievalFilter:
    """检索过滤器：years/document_ids/section_names/source_types/kb_ids 五个维度。

    frozen=True 保证不可变与可哈希（可作为 RetrievalCache key 的字段之一）。
    """

    years: frozenset[int] | None = None
    document_ids: frozenset[str] | None = None
    section_names: frozenset[str] | None = None
    source_types: frozenset[str] | None = None
    kb_ids: frozenset[str] | None = None

    def __post_init__(self):
        # frozen=True 意味着 __init__ 已写完字段；用 object.__setattr__ 在 __post_init__
        # 里把可变集合统一转 frozenset——这样调用方传 list/set 也能用
        object.__setattr__(self, "years", _to_frozenset(self.years))
        object.__setattr__(self, "document_ids", _to_frozenset(self.document_ids))
        object.__setattr__(self, "section_names", _to_frozenset(self.section_names))
        object.__setattr__(self, "source_types", _to_frozenset(self.source_types))
        object.__setattr__(self, "kb_ids", _to_frozenset(self.kb_ids))

    def is_empty(self) -> bool:
        """所有字段为 None 时视为"未设过滤器"——用于 hybrid_search 短路 SQL 拼接。"""
        return all(
            getattr(self, f) is None
            for f in ("years", "document_ids", "section_names", "source_types", "kb_ids")
        )
