"""RegexConflictKeyExtractor — Phase 4-B 实现。

基于 regex 从 chunk text + value_span 提取 5 元组 ConflictKey。
best-effort: 失败字段返回 UNKNOWN / "" / DEFAULT_SCOPE。

设计契约（见 docs/plans/2026-08-23-conflict-key-spec.md）：
- Pure function（无副作用）
- 失败字段必须返回 sentinel（caller 跳过）
- 字段独立：entity / metric / period / unit / scope 各自提取

字段提取规则（spec §4）：
- unit: value_span 之后立刻匹配
- metric: value_span 之前 30 字符内最后匹配
- entity: value_span 之前 30 字符内匹配（产品线/区域模式）
- period: chunk.year
- scope: chunk.section_path 关键词匹配

关联：
- Contract: app/core/conflict_key.py
- Spec: docs/plans/2026-08-23-conflict-key-spec.md
- Plan: docs/plans/2026-08-23-phase4-evidence-contract-repair.md §4-B
"""

from __future__ import annotations

import re

from app.core.conflict_key import DEFAULT_SCOPE, UNKNOWN, ConflictKey
from app.models.schemas import RetrievedChunk


# 单位模式（值之后的字符串）
_UNIT_PATTERN = re.compile(
    r"^(亿元|万元|百万元|千万|百万|十亿|万亿|元|%|个百分点|股|万股|亿股)"
)


# Metric 模式集合 — 每个模式捕获 1 个 group
# 顺序重要：从最具体到最通用（先匹配长前缀再匹配短前缀）
_METRIC_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    # 营业收入类
    (re.compile(r"营业(?:总)?收入"), 0),
    (re.compile(r"销售(?:收入|额)"), 0),
    (re.compile(r"主营(?:业务)?收入"), 0),
    (re.compile(r"国际(?:主营)?(?:业务)?(?:销售)?收入"), 0),
    (re.compile(r"海外(?:主营)?(?:业务)?(?:销售)?收入"), 0),
    (re.compile(r"新能源(?:产品)?(?:销售)?收入"), 0),
    # 净利润类
    (re.compile(r"归属(?:于)?(?:上市公司股东)?的?净利润"), 0),
    (re.compile(r"归属母公司净利润"), 0),
    (re.compile(r"净利润"), 0),
    # 资产负债类
    (re.compile(r"总资产"), 0),
    (re.compile(r"归属(?:于)?(?:上市公司股东)?的?净资产"), 0),
    (re.compile(r"净资产"), 0),
    (re.compile(r"总负债"), 0),
    # 现金流类
    (re.compile(r"经营活动?(?:产生)?的?现金流量净额"), 0),
    (re.compile(r"经营活动?现金(?:流|净额)"), 0),
    (re.compile(r"销售商品提供劳务收到的现金"), 0),
    # 比率类
    (re.compile(r"(?:毛利|营业|净)利率"), 0),
    (re.compile(r"(?:占|占营业)收入(?:比重)?|占比(?:多少)?|占主营(?:业务)?收入"), 0),
    (re.compile(r"回款率"), 0),
    (re.compile(r"市场份额"), 0),
    (re.compile(r"市占率"), 0),
    # 增长率类
    (re.compile(r"(?:同比|环比|年度)?(?:增长|下降|增速|上升)率?"), 0),
    (re.compile(r"(?:同比|环比)?(?:增长|下降|增速|上升)"), 0),
    # 研发类
    (re.compile(r"研发(?:费用|投入|人员)"), 0),
    # 通用"X额"
    (re.compile(r"销售(?:商品)?(?:收入)?额"), 0),
]


# Entity 模式 — 按 (regex, normalized_name) 列表
_ENTITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"挖掘机械"), "挖掘机械"),
    (re.compile(r"混凝土机械"), "混凝土机械"),
    (re.compile(r"起重机械"), "起重机械"),
    (re.compile(r"路面机械"), "路面机械"),
    (re.compile(r"桩工机械"), "桩工机械"),
    (re.compile(r"新能源(?:产品|业务)?"), "新能源"),
    (re.compile(r"国际(?:主营)?(?:业务)?(?:市场|销售)?"), "国际"),
    (re.compile(r"海外(?:主营)?(?:业务)?(?:市场|销售)?"), "国际"),
    (re.compile(r"国内(?:主营)?(?:业务)?(?:市场|销售)?"), "国内"),
    (re.compile(r"融资租赁"), "融资租赁"),
]


# Scope 关键词 → 标准化 scope 名（section_path 头部匹配）
_SCOPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"合并(?:报表|资产负债表|利润表|现金流量表)?"), "合并报表"),
    (re.compile(r"母公司"), "母公司"),
    (re.compile(r"国际(?:业务|市场|销售|主营业务收入)?"), "国际"),
    (re.compile(r"国内(?:业务|市场|销售)?"), "国内"),
    (re.compile(r"海外(?:业务|市场|销售)?"), "国际"),
]


class RegexConflictKeyExtractor:
    """Regex-based ConflictKey extractor.

    Pure function: 多次调用结果一致（无内部状态）。
    Best-effort: 失败字段返回 sentinel，caller 必须跳过含 UNKNOWN 或空 period 的 key。
    """

    # 向前看 metric / entity 的窗口大小（chars）
    LOOKBACK_CHARS = 30

    def extract(
        self,
        chunk: RetrievedChunk,
        value_span: tuple[int, int],
    ) -> ConflictKey:
        """Extract 5-tuple key from chunk around a numeric value span.

        Args:
            chunk: RetrievedChunk with .text / .year / .section_path / .document_id
            value_span: (start, end) char offsets of value in chunk.text

        Returns:
            ConflictKey with all 5 fields populated (best-effort).
        """
        text = chunk.text
        value_start, value_end = value_span
        # 限制在合理范围内
        value_start = max(0, min(value_start, len(text)))
        value_end = max(value_start, min(value_end, len(text)))

        # 1. unit: value_span 之后立刻匹配
        unit = self._extract_unit(text, value_end)

        # 2. metric + 3. entity: 从 value_span 之前 30 字符内提取
        lookback = text[max(0, value_start - self.LOOKBACK_CHARS) : value_start]
        metric = self._extract_metric(lookback)
        entity = self._extract_entity(lookback)

        # 4. period: chunk.year（已经结构化）
        period = (chunk.year or "").strip()

        # 5. scope: chunk.section_path
        scope = self._extract_scope(chunk.section_path or "")

        return ConflictKey(
            entity=entity if entity else UNKNOWN,
            metric=metric if metric else UNKNOWN,
            period=period,
            unit=unit if unit else UNKNOWN,
            scope=scope,
        )

    def _extract_unit(self, text: str, value_end: int) -> str:
        """unit: value_span 之后立刻匹配 (从 value_end 开始的字符串)."""
        tail = text[value_end : value_end + 10]
        m = _UNIT_PATTERN.match(tail)
        return m.group(1) if m else UNKNOWN

    def _extract_metric(self, lookback: str) -> str:
        """metric: 取 lookback 内最接近 value_start 的匹配（last match）。

        Rationale: 中文句子常见「metric A X 单位, 关系 metric B Y 单位」结构。
        当 2 个 metric 共享一段文本时，**距离 value 最近的 metric** 更可能是真正对应
        那个 value 的指标。例子：「国际主营业务收入485亿元,同比增长12.15%」→ 12.15% 对应
        "同比增长" 而不是 "国际主营业务收入"。

        之前的实现（最长匹配）会把同一段文本里的所有 values 标成同一个 metric，导致
        误冲突。last-match 选择语义最贴切的 metric。
        """
        last_match_end = -1
        last_match_text = UNKNOWN
        for pattern, _ in _METRIC_PATTERNS:
            for m in pattern.finditer(lookback):
                if m.end() > last_match_end:
                    last_match_end = m.end()
                    last_match_text = m.group(0)
        return last_match_text

    def _extract_entity(self, lookback: str) -> str:
        """entity: lookback 内第一个匹配的 entity 模式（左→右扫描）。"""
        for pattern, name in _ENTITY_PATTERNS:
            if pattern.search(lookback):
                return name
        return "公司整体"  # 默认：不是产品线就是公司整体

    def _extract_scope(self, section_path: str) -> str:
        """scope: section_path 前 50 字符内匹配关键词。"""
        head = section_path[:50]
        for pattern, name in _SCOPE_PATTERNS:
            if pattern.search(head):
                return name
        return DEFAULT_SCOPE


# 模块级单例
regex_conflict_key_extractor = RegexConflictKeyExtractor()
