"""LLM-based metadata generation for each chunk.

Uses a single MiniMax API call to generate title / summary / questions
for ALL chunks in one batch, then writes results back into the Chunk objects.
"""

import asyncio
import logging

from app.llm.chat import minimax_client
from app.llm.base import robust_json_parse
from app.ingestion.chunker import Chunk

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
            resp = asyncio.run(minimax_client.chat(
                [{"role": "user", "content": prompt}],
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
