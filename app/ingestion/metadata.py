"""LLM-based metadata generation for each chunk.

Uses a single MiniMax API call to generate title / summary / questions
for ALL chunks in one batch, then writes results back into the Chunk objects.

性能注意:
  - prompt 已精简(只问 title/summary/questions,不夹带图片说明)
  - max_tokens=1024(实际输出 ~200 token)避免浪费
  - 单次 LLM 调用通常 1-2s
"""

import json
import logging

from app.llm.chat import minimax_client
from app.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)


METADATA_PROMPT = """为以下每个文本块生成元数据。每个块输出:
1. title:5-10 字短标题
2. summary:1 句话概括
3. questions:可能回答的 3 个用户问题

文本块:
{chunks_text}

只返回 JSON(无 markdown 包裹):
{{"chunks":[{{"index":0,"title":"...","summary":"...","questions":["?","?","?"]}}, ...]}}"""


class ChunkMetadataGenerator:
    """Calls MiniMax once for all chunks to generate title/summary/questions per chunk."""

    def generate(self, chunks: list[Chunk]) -> list[Chunk]:
        if not chunks:
            return chunks

        chunks_text = "\n\n".join(
            f"【{i}】\n{c.text[:300]}" for i, c in enumerate(chunks)
        )

        prompt = METADATA_PROMPT.format(chunks_text=chunks_text)

        try:
            resp = minimax_client.chat([{"role": "user", "content": prompt}], max_tokens=1024, timeout=15)
            if not resp or not resp.strip():
                logger.warning("Metadata generation returned empty response for %d chunks", len(chunks))
                return chunks
            cleaned = resp.strip()
            cleaned = cleaned.removeprefix("```json").removesuffix("```").strip()
            if not cleaned:
                logger.warning("Metadata response empty after cleaning for %d chunks", len(chunks))
                return chunks
            data = json.loads(cleaned)
            for item in data.get("chunks", []):
                idx = item.get("index")
                if idx is not None and 0 <= idx < len(chunks):
                    chunks[idx].title = item.get("title", chunks[idx].title)
                    chunks[idx].summary = item.get("summary", "")
                    chunks[idx].questions = item.get("questions", [])
        except Exception:
            logger.warning("Metadata generation failed for %d chunks (non-fatal)", len(chunks))

        return chunks


chunk_metadata_generator = ChunkMetadataGenerator()
