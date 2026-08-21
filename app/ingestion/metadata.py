"""LLM-based metadata generation for each chunk.

Uses a single MiniMax API call to generate title / summary / questions
for ALL chunks in one batch, then writes results back into the Chunk objects.

Also provides embedding_text enhancement using a small model (Qwen2.5-7B-Instruct)
to improve retrieval quality for financial data queries.
"""

import asyncio
import logging
import re

from app.llm.chat import minimax_client
from app.llm.base import robust_json_parse, call_llm_with_retry
from app.ingestion.chunker import Chunk
from app.config import settings

logger = logging.getLogger(__name__)


_FMT = """你是一个企业知识库元数据生成器。
为每个文本块生成 title（10-20字精确标题）、summary（2-3句话，保留数字/日期/条件）、questions（4-5个具体业务问题）。

只输出 JSON，格式：
{{"chunks":[{{"index":0,"title":"...","summary":"...","questions":["?","?","?","?"]}}, ...]}}

--- 示例1：技术参数块 ---
【0】
路径：产品规格 / 技术参数
内容：M3 工业网关工作温度范围-40℃~85℃，支持 Modbus RTU/TCP、OPC UA、S7、MC 等 20+ 工业协议，配备 2 个千兆网口和 4G 模块。
→ {{"chunks":[{{"index":0,"title":"M3工作温度与协议支持","summary":"M3网关可在-40℃~85℃宽温下工作，南向支持Modbus、OPC UA、西门子S7等20余种工业协议，北向支持MQTT/HTTP，配备双千兆网口和4G无线模块。","questions":["M3网关的工作温度范围是多少？","M3支持哪些工业协议？","M3网关的网络接口配置如何？","M3是否支持4G无线通信？"]}}]}}

--- 示例2：财务数据块 ---
【0】
路径：财务分析 / Q4营收
内容：Q4华东战区营收1.2亿（完成率112%），华南0.98亿（91%），华北0.75亿（83%），西南0.42亿（105%）。合计3.35亿。
→ {{"chunks":[{{"index":0,"title":"Q4各战区营收与完成率","summary":"Q4四大战区合计营收3.35亿元。华东完成率最高（112%），西南次之（105%），华北最低（83%）。各战区完成率差异显著，华东和西南超额完成目标。","questions":["Q4营收最高的战区是哪个？完成率多少？","华东战区Q4营收目标完成率是多少？","Q4四大战区合计营收多少？","哪个战区Q4完成率最低？","西南战区Q4营收完成情况如何？"]}}]}}

路径指示文档位置，内容为实际文本。
{chunks_text}"""

METADATA_PROMPT = _FMT


class ChunkMetadataGenerator:
    """Calls MiniMax once for all chunks to generate title/summary/questions per chunk."""

    def generate(self, chunks: list[Chunk]) -> list[Chunk]:
        if not chunks:
            return chunks

        chunks_text = "\n\n".join(
            f"【{i}】\n路径：{' / '.join(c.section_path) if c.section_path else '无'}\n内容：{c.text[:300]}"
            for i, c in enumerate(chunks)
        )

        prompt = METADATA_PROMPT.format(chunks_text=chunks_text)
        ntoks = max(1024, len(chunks) * 256)

        try:
            # 经统一策略层：单次客户端 + 类型感知重试（chat 本身不再重试）
            resp = asyncio.run(call_llm_with_retry(
                minimax_client.chat,
                [{"role": "user", "content": prompt}],
                tag="metadata",
                max_retries=1,
                max_tokens=ntoks,
                timeout=min(120, 15 * len(chunks)),
            ))
            if not resp or not resp.strip():
                logger.warning("Metadata generation returned empty response for %d chunks", len(chunks))
                return chunks
            data = robust_json_parse(resp)
            if not data:
                logger.warning("No JSON found in metadata response for %d chunks", len(chunks))
                return chunks
            for item in data.get("chunks", []):
                idx = item.get("index")
                if idx is not None and 0 <= idx < len(chunks):
                    new_title = item.get("title")
                    if new_title:
                        chunks[idx].title = new_title
                    new_summary = item.get("summary")
                    if new_summary:
                        chunks[idx].summary = new_summary
                    new_questions = item.get("questions")
                    if new_questions:
                        chunks[idx].questions = new_questions
        except Exception:
            logger.exception("Metadata generation failed for %d chunks (non-fatal)", len(chunks))

        return chunks


chunk_metadata_generator = ChunkMetadataGenerator()


# ── Embedding Text Enhancement ──────────────────────────────────────────────

_EMBEDDING_TEXT_PROMPT = """你是一个用于 RAG 检索的文本增强器。

任务：从文档 chunk 中提取关键信息，生成一段高信息密度的检索文本。

【严格要求】
1. 所有数字必须**逐字复制**原文，不要计算、不要四舍五入、不要估算
2. 百分比必须**逐字复制**原文（如"5.08%"不能改成"5%"）
3. 必须包含所有年份信息（如"2024年""2023年"）
4. 保持原始数据的对应关系（哪个指标对应哪个数值）

【输出格式】
用自然语言描述，包含：指标名称、年份、数值、单位、同比变化（如有）
例如："三一重工2024年营业收入777.73亿元，2023年740.19亿元，同比增长5.08%"

【禁止】
- 不要重新组织或重新计算数据
- 不要添加原文没有的信息
- 不要解释，不要 markdown，不要 JSON

原始 chunk：
{content}

输出："""


# Day 2 上午：EmbeddingTextEnhancer 已弃用 — 详见 docs/plans/2026-08-22-rag-decomposition.md §一.3 + §九 Day 2 上午。


