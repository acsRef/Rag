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
                    chunks[idx].summary = item.get("summary", "")
                    chunks[idx].questions = item.get("questions", [])
        except Exception:
            logger.exception("Metadata generation failed for %d chunks (non-fatal)", len(chunks))

        return chunks


chunk_metadata_generator = ChunkMetadataGenerator()
