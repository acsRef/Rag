"""Evidence organization layer — 检索结果 → 结构化证据表。

职责：
1. 按子问题归类 chunks
2. 标注覆盖度（哪些子问题有证据、哪些缺失）
3. 去重（同一 chunk 支撑多个子问题时只出现一次）
4. 生成 LLM 可读的证据表格式

设计原则：
- 纯逻辑模块，不依赖 DB / LLM / 网络
- 失败时降级为原有散装 chunks 格式
- 通用设计，不依赖特定文档类型（年报/合同/手册均可）

Day 2 下午（plan §五）：
- 新增 EvidenceResult dataclass（不替代 EvidenceTable，只包装暴露 plan §五.1 的 5 字段）
- 新增 build_evidence_result(table) 转换
- 新增 evidence_gate_should_refuse(result, threshold) 给 pipeline.py:300 evidence_gate 用
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.schemas import RetrievedChunk

logger = logging.getLogger(__name__)


# ── Day 2 下午新增：EvidenceResult 包装 ────────────────────────────────────────


@dataclass
class EvidenceResult:
    """plan §五.1 暴露给 pipeline 的扁平视图。

    EvidenceTable 仍然存在（314 行活代码不动）；EvidenceResult 是 pipeline 关心的
    5 字段包装——coverage / temporal_consistent / conflicts / sources / coverage_by_year。

    注意：
    - coverage 在 build_evidence_result 中 clip 到 [0, 1]（避免下游误读）
    - sources 是 [{chunk_id, document_id}] 列表（pipeline 用 chunk_id 查 source）
    - coverage_by_year 默认 {}：RetrievedChunk.year 字段未填时不下结论
    """

    coverage: float
    temporal_consistent: bool
    conflicts: list[Any]
    sources: list[dict]
    coverage_by_year: dict[str, float] = field(default_factory=dict)


def build_evidence_result(table: "EvidenceTable") -> EvidenceResult:
    """EvidenceTable → EvidenceResult 转换。

    字段语义：
    - coverage = covered_slots / total_slots，clip 到 [0, 1]
    - temporal_consistent = (len(conflicts) == 0)——plan §五.1 把"无冲突"视为时序一致
    - sources = [{chunk_id, document_id}] 列表，覆盖所有 slot 的去重 chunk
    - coverage_by_year：每个年份覆盖的 slot 比例；RetrievedChunk.year 为空时不下结论（默认 {}）
    """
    total_slots = len(table.slots)
    if total_slots == 0:
        return EvidenceResult(
            coverage=0.0,
            temporal_consistent=True,
            conflicts=list(table.conflicts),
            sources=[],
            coverage_by_year={},
        )

    covered = sum(1 for s in table.slots if s.covered)
    coverage = max(0.0, min(1.0, covered / total_slots))

    sources: list[dict] = []
    seen: set[str] = set()
    for slot in table.slots:
        for chunk in slot.chunks:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            sources.append({"chunk_id": chunk.chunk_id, "document_id": chunk.document_id})

    # coverage_by_year：按年份统计该年 chunk 覆盖了多少 slot
    coverage_by_year: dict[str, float] = {}
    year_to_slots: dict[str, set[int]] = {}
    for i, slot in enumerate(table.slots):
        for chunk in slot.chunks:
            year = (chunk.year or "").strip()
            if not year:
                continue
            year_to_slots.setdefault(year, set()).add(i)
    for year, slot_idxs in year_to_slots.items():
        coverage_by_year[year] = len(slot_idxs) / total_slots

    return EvidenceResult(
        coverage=coverage,
        temporal_consistent=(len(table.conflicts) == 0),
        conflicts=list(table.conflicts),
        sources=sources,
        coverage_by_year=coverage_by_year,
    )


def evidence_gate_should_refuse(result: EvidenceResult, threshold: float) -> bool:
    """plan §五.2 evidence_gate 决策：是否拒答/追问。

    拒答条件（任一）：
    1. coverage < threshold（证据不足）
    2. temporal_consistent is False（有冲突，需追问确认）

    返回 True 表示应拒答；False 表示证据充分可生成。
    threshold=0 时永远不拒答（边界情况，用于禁用 gate）。
    """
    if threshold <= 0:
        return False
    if not result.temporal_consistent:
        return True
    if result.coverage < threshold:
        return True
    return False


# ── Data Models ─────────────────────────────────────────────────────────


@dataclass
class EvidenceSlot:
    """一个证据槽位 = 一个子问题 + 支撑它的 chunks。"""

    sub_question: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    doc_ids: set[str] = field(default_factory=set)

    @property
    def covered(self) -> bool:
        return len(self.chunks) > 0

    def __post_init__(self):
        self.doc_ids = {c.document_id for c in self.chunks if c.document_id}


@dataclass
class EvidenceTable:
    """完整的证据表 — 多个子问题的证据槽集合。"""

    query: str
    slots: list[EvidenceSlot] = field(default_factory=list)
    query_type: str = "complex"  # "simple" | "complex"
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def overall_coverage(self) -> float:
        if not self.slots:
            return 0.0
        covered = sum(1 for s in self.slots if s.covered)
        return covered / len(self.slots)

    @property
    def all_doc_ids(self) -> set[str]:
        docs: set[str] = set()
        for s in self.slots:
            docs.update(s.doc_ids)
        return docs

    @property
    def has_multiple_docs(self) -> bool:
        return len(self.all_doc_ids) > 1


# ── Conflict Detection & Resolution ────────────────────────────────────


@dataclass
class MetricValue:
    """从 chunk 中提取的一个指标数值。"""
    metric: str          # 指标名称（如"营业收入"）
    value: float         # 数值
    unit: str            # 单位（如"亿元"）
    raw_text: str        # 原始文本片段
    chunk_id: str        # 来源 chunk
    doc_id: str          # 来源文档
    section_path: str    # 来源章节
    year: str            # 年份（如果有）


@dataclass
class Conflict:
    """检测到的冲突：同一指标在不同来源中有不同数值。"""
    metric: str                           # 指标名称
    values: list[MetricValue]             # 不同来源的数值
    conflict_type: str = "value_mismatch" # "value_mismatch" | "year_mismatch" | "section_mismatch"
    severity: str = "medium"              # "high" | "medium" | "low"
    resolution_hint: str = ""             # 消解建议


# ── Conflict Detector ──────────────────────────────────────────────────

# 数值提取正则：匹配 "数字+单位" 模式
# 支持格式：732.22亿元、1,234万元、56.7%、-3.2%
_VALUE_PATTERN = re.compile(
    r'(-?[\d,]+(?:\.\d+)?)\s*'
    r'(亿元|万元|百万元|亿元|千万|百万|十亿|万亿|%|个百分点|股|万股|亿股)',
)

# 指标名前缀模式：数值前面的指标名
_METRIC_PREFIX_PATTERN = re.compile(
    r'([一-鿿]{2,15}(?:收入|利润|资产|负债|现金流|销量|产量|占比|增速|增长|下降|规模|总额|合计))',
)


class ConflictDetector:
    """检测同一指标在不同文档/章节中的数值冲突。

    工作流程：
    1. 从每个 chunk 中提取 (指标名, 数值, 单位) 三元组
    2. 按指标名分组
    3. 如果同一指标有多个不同数值 → 标记为冲突
    4. 按冲突类型分类并给出消解建议
    """

    def detect(self, table: EvidenceTable) -> list[Conflict]:
        """扫描证据表，检测数值冲突。"""
        if not table.slots or not table.has_multiple_docs:
            return []

        # Step 1: 从所有 chunks 提取指标值
        all_values: list[MetricValue] = []
        for slot in table.slots:
            for chunk in slot.chunks:
                extracted = self._extract_metric_values(chunk)
                all_values.extend(extracted)

        if not all_values:
            return []

        # Step 2: 按指标名分组
        by_metric: dict[str, list[MetricValue]] = {}
        for mv in all_values:
            key = self._normalize_metric(mv.metric)
            by_metric.setdefault(key, []).append(mv)

        # Step 3: 检测冲突（同一指标有多个不同数值）
        conflicts: list[Conflict] = []
        for metric_key, values in by_metric.items():
            if len(values) < 2:
                continue

            # 按数值去重
            unique_values = set()
            for v in values:
                unique_values.add((v.value, v.unit))

            if len(unique_values) <= 1:
                continue  # 所有来源数值一致，无冲突

            # 有冲突！分类并生成消解建议
            conflict = self._classify_conflict(metric_key, values)
            conflicts.append(conflict)

        if conflicts:
            logger.info("conflict.detected count=%d metrics=%s",
                        len(conflicts), [c.metric for c in conflicts])

        return conflicts

    def _extract_metric_values(self, chunk: RetrievedChunk) -> list[MetricValue]:
        """从 chunk 文本中提取指标数值。"""
        results: list[MetricValue] = []
        text = chunk.text

        for match in _VALUE_PATTERN.finditer(text):
            raw_num = match.group(1).replace(",", "")
            try:
                value = float(raw_num)
            except ValueError:
                continue
            unit = match.group(2)

            # 往前找指标名
            prefix_start = max(0, match.start() - 20)
            prefix_text = text[prefix_start:match.start()]
            metric_match = _METRIC_PREFIX_PATTERN.findall(prefix_text)
            metric = metric_match[-1] if metric_match else "未知指标"

            # 提取上下文片段
            ctx_start = max(0, match.start() - 30)
            ctx_end = min(len(text), match.end() + 10)
            raw_text = text[ctx_start:ctx_end].strip()

            results.append(MetricValue(
                metric=metric,
                value=value,
                unit=unit,
                raw_text=raw_text,
                chunk_id=chunk.chunk_id,
                doc_id=chunk.document_id,
                section_path=chunk.section_path,
                year=chunk.year,
            ))

        return results

    def _normalize_metric(self, metric: str) -> str:
        """归一化指标名（去除修饰词，保留核心语义）。"""
        # 去除年份、同比、环比等修饰
        normalized = re.sub(r'(同比|环比|本期|上期|当年|历年)', '', metric)
        return normalized.strip()

    def _classify_conflict(self, metric: str, values: list[MetricValue]) -> Conflict:
        """分类冲突类型并生成消解建议。"""
        # 检查是否因年份不同导致的"冲突"
        years = {v.year for v in values if v.year}
        docs = {v.doc_id for v in values}

        if len(years) > 1 and len(years) == len(values):
            # 每个值来自不同年份 → 不是真冲突，是时间差异
            return Conflict(
                metric=metric,
                values=values,
                conflict_type="year_mismatch",
                severity="low",
                resolution_hint="各数值来自不同年份，非真实冲突。回答时按年份分别列出即可。",
            )

        if len(docs) > 1:
            # 不同文档的同一指标数值不同
            # 检查是否因章节权威性不同
            sections = {v.section_path for v in values if v.section_path}
            if len(sections) > 1:
                return Conflict(
                    metric=metric,
                    values=values,
                    conflict_type="section_mismatch",
                    severity="medium",
                    resolution_hint=(
                        f"指标「{metric}」在不同章节有不同数值。"
                        "建议优先采用「主要会计数据」「财务报告」等权威章节的数据。"
                    ),
                )

            return Conflict(
                metric=metric,
                values=values,
                conflict_type="value_mismatch",
                severity="high",
                resolution_hint=(
                    f"指标「{metric}」在不同文档中数值不一致（"
                    + "、".join(f"{v.value}{v.unit}" for v in values[:3])
                    + "）。请检查是否为数据重述/调整，并标注来源。"
                ),
            )

        # 同一文档内的冲突
        return Conflict(
            metric=metric,
            values=values,
            conflict_type="value_mismatch",
            severity="high",
            resolution_hint=(
                f"指标「{metric}」在同一文档中有多个不同数值，请检查上下文确认哪个是当前有效值。"
            ),
        )


# ── Evidence Organizer ──────────────────────────────────────────────────


class EvidenceOrganizer:
    """将子问题→chunks 映射整理为结构化证据表，并格式化为 LLM 可读文本。"""

    def organize(
        self,
        query: str,
        sub_question_chunks: dict[str, list[RetrievedChunk]],
        query_type: str = "complex",
    ) -> EvidenceTable:
        """构建证据表。

        Args:
            query: 用户原始问题
            sub_question_chunks: {子问题文本: [chunks]} 映射
            query_type: 问题类型 ("simple" / "complex")

        Returns:
            EvidenceTable 结构化证据表
        """
        slots: list[EvidenceSlot] = []

        for sub_q, chunks in sub_question_chunks.items():
            # 去重：同一 chunk_id 只保留一次（可能出现在多个子问题的结果中）
            seen_chunk_ids: set[str] = set()
            unique_chunks: list[RetrievedChunk] = []
            for c in chunks:
                if c.chunk_id not in seen_chunk_ids:
                    seen_chunk_ids.add(c.chunk_id)
                    unique_chunks.append(c)

            slot = EvidenceSlot(
                sub_question=sub_q,
                chunks=unique_chunks,
            )
            slots.append(slot)

        table = EvidenceTable(
            query=query,
            slots=slots,
            query_type=query_type,
        )

        # P3: 冲突检测
        if table.has_multiple_docs:
            detector = ConflictDetector()
            table.conflicts = detector.detect(table)

        logger.info(
            "evidence.organize query=%s slots=%d coverage=%.2f docs=%d conflicts=%d",
            query[:40], len(slots), table.overall_coverage, len(table.all_doc_ids),
            len(table.conflicts),
        )

        return table

    # ── Evidence-Aware Reranking ─────────────────────────────────────

    def compute_chunk_importance(
        self,
        table: EvidenceTable,
    ) -> dict[str, float]:
        """为每个 chunk 计算证据重要性分数。

        根据问题类型差异化打分：
        - comparison（对比类）：成对证据优先——每个子问题都需要有来自不同文档的证据
        - summary（汇总类）：覆盖率优先——覆盖更多子问题的文档更重要
        - 默认：保持原始检索分数排序

        Returns:
            {chunk_id: importance_score} 映射
        """
        if not table.slots:
            return {}

        scores: dict[str, float] = {}
        n_slots = len(table.slots)

        # 计算每个文档覆盖了多少个子问题（用于 summary 类加权）
        doc_slot_coverage: dict[str, int] = {}
        for slot in table.slots:
            for chunk in slot.chunks:
                doc_id = chunk.document_id
                if doc_id:
                    doc_slot_coverage[doc_id] = doc_slot_coverage.get(doc_id, 0) + 1

        for slot in table.slots:
            slot_doc_ids = slot.doc_ids
            n_slot_docs = len(slot_doc_ids)

            for chunk in slot.chunks:
                # 基础分：原始检索分数（归一化到 0-1）
                base_score = chunk.score

                # 证据覆盖贡献
                coverage_bonus = 0.0

                if table.query_type == "comparison" or n_slots > 1:
                    # 对比类/多子问题：每个子问题都有证据时加 bonus
                    # 如果这个 chunk 所在的子问题只有 1 个文档的证据，提升重要性
                    if n_slot_docs == 1:
                        coverage_bonus += 0.1  # 独苗文档，需要被保留
                    # 如果这个 chunk 来自一个其他子问题还没有的文档，提升重要性
                    other_docs = set()
                    for other_slot in table.slots:
                        if other_slot is not slot:
                            other_docs.update(other_slot.doc_ids)
                    unique_docs = slot_doc_ids - other_docs
                    if chunk.document_id in unique_docs:
                        coverage_bonus += 0.15  # 独有来源，非常重要

                if table.query_type == "summary" or n_slots > 1:
                    # 汇总类：覆盖更多子问题的文档更重要
                    doc_coverage = doc_slot_coverage.get(chunk.document_id, 0)
                    coverage_ratio = doc_coverage / n_slots if n_slots > 0 else 0
                    coverage_bonus += coverage_ratio * 0.1

                scores[chunk.chunk_id] = base_score + coverage_bonus

        return scores

    def rerank_chunks(
        self,
        chunks: list[RetrievedChunk],
        table: EvidenceTable,
    ) -> list[RetrievedChunk]:
        """根据证据重要性对 chunks 重排序。

        返回按重要性降序排列的新列表（不修改原列表）。
        """
        importance = self.compute_chunk_importance(table)
        if not importance:
            return list(chunks)

        # 按重要性降序排列，相同重要性保持原序（stable sort）
        return sorted(
            chunks,
            key=lambda c: importance.get(c.chunk_id, c.score),
            reverse=True,
        )

    def detect_comparison_pattern(self, query: str, sub_questions: list[str]) -> bool:
        """检测是否为对比类问题。

        通过子问题结构判断：如果多个子问题问的是同一指标的不同维度
        （如不同年份、不同实体），则为对比类。
        """
        if len(sub_questions) < 2:
            return False

        # 检查子问题是否有共同的关键词模式（同一指标、不同维度）
        # 简单启发式：子问题长度相近且共享大部分词汇
        import re
        # 提取每个子问题的关键词（去掉年份/数字差异）
        normalized = []
        for sq in sub_questions:
            # 去掉年份和数字
            norm = re.sub(r'\d{4}', '', sq)
            norm = re.sub(r'\d+', '', norm)
            norm = norm.strip()
            if norm:
                normalized.append(norm)

        if len(normalized) < 2:
            return False

        # 检查归一化后的子问题是否高度相似（只差年份/数字）
        # 简单方法：检查第一个子问题去掉数字后是否与其他子问题相同
        base = normalized[0]
        similar_count = sum(1 for n in normalized[1:] if n == base)

        # 如果超过一半的子问题归一化后相同，认为是对比类
        return similar_count >= len(normalized) // 2

    def format_for_prompt(self, table: EvidenceTable) -> str:
        """将证据表格式化为 LLM 可读的结构化文本。

        格式：
        ## 证据表
        ### 子问题 1：...
        - [Source N] 文档标题 · 章节路径
          "内容摘要..."
        ### 子问题 2：...
        ...
        ### 覆盖度
        ✅ N/M 子问题有证据支撑
        """
        if not table.slots:
            return ""

        # 构建全局 source 编号映射（chunk_id → source number）
        source_map: dict[str, int] = {}
        source_num = 1
        for slot in table.slots:
            for chunk in slot.chunks:
                if chunk.chunk_id not in source_map:
                    source_map[chunk.chunk_id] = source_num
                    source_num += 1

        parts: list[str] = ["## 证据表"]

        for i, slot in enumerate(table.slots):
            parts.append(f"\n### 子问题 {i + 1}：{slot.sub_question}")

            if not slot.chunks:
                parts.append("_（未找到直接相关的证据）_")
                continue

            for chunk in slot.chunks:
                src_num = source_map.get(chunk.chunk_id, 0)
                # 构建来源标签：[Source N] 文档标题 · 章节路径
                label_parts = [f"[Source {src_num}]"]

                # 文档标题（优先 title，其次 section_path）
                if chunk.title:
                    label_parts.append(chunk.title)
                if chunk.section_path:
                    label_parts.append(chunk.section_path)

                label = " · ".join(label_parts) if len(label_parts) > 1 else label_parts[0]

                # 文本摘要（取前 300 字符）
                text_preview = chunk.text[:300].replace("\n", " ").strip()
                if len(chunk.text) > 300:
                    text_preview += "..."

                parts.append(f"- {label}\n  \"{text_preview}\"")

        # 覆盖度汇总
        parts.append(f"\n### 覆盖度")
        covered_count = sum(1 for s in table.slots if s.covered)
        total_count = len(table.slots)

        if covered_count == total_count:
            parts.append(f"✅ {covered_count}/{total_count} 子问题均有证据支撑")
        elif covered_count > 0:
            parts.append(f"⚠️ {covered_count}/{total_count} 子问题有证据支撑")
            # 列出缺失的子问题
            missing = [s.sub_question for s in table.slots if not s.covered]
            for m in missing:
                parts.append(f"  - 缺失：{m}")
        else:
            parts.append(f"❌ 未找到与问题相关的证据")

        # 多文档提示
        if table.has_multiple_docs:
            doc_count = len(table.all_doc_ids)
            parts.append(f"\n📚 证据来自 {doc_count} 份不同文档，请综合所有文档的信息回答。")

        # P3: 冲突提示
        if table.conflicts:
            parts.append("\n### ⚠️ 数据冲突提示")
            for conflict in table.conflicts:
                if conflict.conflict_type == "year_mismatch":
                    # 低严重度：只是不同年份，不需要特别警告
                    continue
                severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(conflict.severity, "⚪")
                parts.append(f"\n{severity_icon} **{conflict.metric}** 存在数值差异：")
                for mv in conflict.values[:4]:  # 最多展示 4 个
                    source_label = f"[Source {mv.chunk_id[:8]}]"
                    ctx = mv.raw_text[:50]
                    parts.append(f"  - {source_label} {mv.doc_id[:8]}·{mv.section_path or '未知章节'}: \"{ctx}\"")
                if conflict.resolution_hint:
                    parts.append(f"  💡 {conflict.resolution_hint}")

        return "\n".join(parts)

    def get_source_map(self, table: EvidenceTable) -> dict[str, int]:
        """获取 chunk_id → source number 的映射，供 frontend sources 对齐。"""
        source_map: dict[str, int] = {}
        source_num = 1
        for slot in table.slots:
            for chunk in slot.chunks:
                if chunk.chunk_id not in source_map:
                    source_map[chunk.chunk_id] = source_num
                    source_num += 1
        return source_map

    def get_all_chunks(self, table: EvidenceTable) -> list[RetrievedChunk]:
        """从证据表中提取所有去重后的 chunks（保持首次出现顺序）。"""
        seen: set[str] = set()
        result: list[RetrievedChunk] = []
        for slot in table.slots:
            for chunk in slot.chunks:
                if chunk.chunk_id not in seen:
                    seen.add(chunk.chunk_id)
                    result.append(chunk)
        return result


# ── Module singleton ────────────────────────────────────────────────────

evidence_organizer = EvidenceOrganizer()
