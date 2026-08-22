"""ConflictKey — 5-tuple comparison key for cross-source value comparison.

**Phase 4-A 数据契约**：先定义 key 长什么样，不写 detector/extractor 实现。

设计原则：
- Conflict 语义：相同 (entity, metric, period, unit, scope) 但不同 value = TRUE conflict
- 任何 5 元组字段不同 = NOT a conflict（不同事物）
- 模块 pure：不依赖 LLM/DB/网络；只承载数据结构
- frozen dataclass → 可哈希，可作为 dict key

Phase 4-B 才实现 `ConflictKeyExtractor`；本模块仅定义 Protocol 接口。

关联：
- 审计：[docs/plans/2026-08-23-p0-audit-report.md](../../docs/plans/2026-08-23-p0-audit-report.md) §ConflictDetector
- 详细 spec：[docs/plans/2026-08-23-conflict-key-spec.md](../../docs/plans/2026-08-23-conflict-key-spec.md)
- Phase 4 plan：[docs/plans/2026-08-23-phase4-evidence-contract-repair.md](../../docs/plans/2026-08-23-phase4-evidence-contract-repair.md)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models.schemas import RetrievedChunk

# ── Sentinels ────────────────────────────────────────────────────────────────

# 无法识别时的兜底值。带 "未知" 的 ConflictKey 应被 caller 跳过 — 不参与 conflict check
UNKNOWN = "未知"

# 无具体 scope 时的兜底（如「公司实现营业总收入」没有限定合并/母公司）
DEFAULT_SCOPE = "公司整体"


# ── ConflictKey dataclass ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ConflictKey:
    """5-tuple comparison key for cross-source value comparison.

    Two MetricValues can be in conflict ONLY IF their ConflictKeys match on
    all 5 fields. Any difference in these fields means they describe
    different things → NOT a conflict.

    Frozen dataclass → hashable, can be used as dict key.

    字段含义：
    - entity:  产品线（挖掘/混凝土/起重）/ 部门 / 区域 / "公司整体" / UNKNOWN
    - metric:  营业收入 / 净利润 / 毛利率 / 销售收入 / UNKNOWN
    - period:  2023年 / 2024年 / 2024Q1 / chunk.year / "" (空=不可比)
    - unit:    亿元 / 万元 / % / 个百分点 / 股 / UNKNOWN
    - scope:   合并报表 / 母公司 / 国际 / 国内 / 海外 / DEFAULT_SCOPE
    """

    entity: str
    metric: str
    period: str
    unit: str
    scope: str

    def matches_except_value(self, other: ConflictKey) -> bool:
        """Return True if all 5 fields match.

        Used by ConflictDetector: only keys with matching 5-tuple can be
        conflicts. Values are not part of the key (they live in MetricValue).
        """
        return (
            self.entity == other.entity
            and self.metric == other.metric
            and self.period == other.period
            and self.unit == other.unit
            and self.scope == other.scope
        )


# ── ConflictKeyExtractor Protocol ───────────────────────────────────────────


class ConflictKeyExtractor(Protocol):
    """Interface for extracting ConflictKey from a chunk.

    **Phase 4-B 实现**：先 regex-based (`RegexConflictKeyExtractor`)，后续可能加
    LLM-based (`LLMConflictKeyExtractor`) 兜底。

    设计契约：
    - Pure function：无副作用，可独立测试
    - Best-effort：失败字段返回 UNKNOWN / "" / DEFAULT_SCOPE
    - 返回的 key 若包含 UNKNOWN 或空 period，caller 必须跳过（不可比）
    """

    def extract(
        self,
        chunk: RetrievedChunk,
        value_span: tuple[int, int],
    ) -> ConflictKey:
        """Extract 5-tuple key from chunk around a numeric value span.

        Args:
            chunk: RetrievedChunk with text + metadata (year, document_id, ...)
            value_span: (start, end) character offsets of the value in
                        chunk.text. The extractor should look at context
                        BEFORE the value (entity, metric) and chunk metadata
                        (year, scope) to populate the key.

        Returns:
            ConflictKey with all 5 fields populated (best-effort).
            Failed extractions:
              - entity/metric → UNKNOWN
              - period → chunk.year or "" (empty = 不可比)
              - unit → UNKNOWN
              - scope → DEFAULT_SCOPE
        """
        ...
