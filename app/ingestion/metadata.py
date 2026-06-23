"""LLM-based metadata generation for each chunk.

Uses a single MiniMax API call to generate title / summary / questions
for ALL chunks in one batch, then writes results back into the Chunk objects.
"""

import json
import logging

from app.llm.chat import minimax_client
from app.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)


METADATA_PROMPT = """你是一个文档分析助手。为以下每个文本块生成元数据。

对每个文本块，输出：
1. title：简短的段落标题（5-10字）
2. summary：1-2句话摘要，概括核心内容
3. questions：该段落可能回答的3个用户问题

文本块列表：
{chunks_text}

返回严格 JSON 格式（不要 markdown 包裹）：
{{"chunks": [
  {{"index": 0, "title": "...", "summary": "...", "questions": ["?", "?", "?"]}},
  ...
]}}"""


class ChunkMetadataGenerator:
    """Calls MiniMax once for all chunks to generate title/summary/questions per chunk."""

    def generate(self, chunks: list[Chunk]) -> list[Chunk]:
        if not chunks:
            return chunks

        # Concatenate first 300 chars of each chunk with an index marker
        chunks_text = "\n\n".join(
            f"【{i}】\n{c.text[:300]}" for i, c in enumerate(chunks)
        )

        prompt = METADATA_PROMPT.format(chunks_text=chunks_text)

        try:
            resp = minimax_client.chat([{"role": "user", "content": prompt}])
            data = json.loads(resp.strip().removeprefix("```json").removesuffix("```").strip())
            for item in data.get("chunks", []):
                idx = item.get("index")
                if idx is not None and 0 <= idx < len(chunks):
                    chunks[idx].title = item.get("title", chunks[idx].title)
                    chunks[idx].summary = item.get("summary", "")
                    chunks[idx].questions = item.get("questions", [])
        except Exception:
            logger.exception("Metadata generation failed for %d chunks", len(chunks))

        return chunks


chunk_metadata_generator = ChunkMetadataGenerator()
