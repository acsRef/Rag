"""Structure-aware semantic chunker.

Chunking priority: heading > paragraph > line > sentence > character boundary.
Atomic blocks (code, table, image) are never split; oversized atomic blocks
can go up to max_atomic. Overlap borrows last N sentences from previous chunk.
"""

import re
from dataclasses import dataclass, field

from app.ingestion.structurer import Element


@dataclass
class Chunk:
    """A single document chunk with metadata to be filled later by ChunkMetadataGenerator."""
    text: str = ""
    title: str = ""
    summary: str = ""
    questions: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)


class TextChunker:
    """Splits structured sections into chunks with atomic-block protection and overlap."""

    def __init__(
        self,
        chunk_size: int = 512,
        overlap_sentences: int = 3,
        max_atomic: int = 1024,
        borrow_ratio: float = 0.5,
    ):
        self.chunk_size = chunk_size
        self.overlap_sentences = overlap_sentences
        self.max_atomic = max_atomic  # max chars an atomic block can occupy alone
        self.borrow_ratio = borrow_ratio  # fraction of chunk_size used to decide buffer trimming before atomic merge

    def chunk(
        self,
        sections: list,
        use_semantic: bool = False,
    ) -> list[Chunk]:
        """Main entry point: iterate sections, accumulate a buffer, emit chunks when buffer exceeds chunk_size.

        Atomic blocks are emitted immediately or merged with the preceding buffer
        (borrowing context sentences if the buffer would be too large).
        """
        if not sections:
            return []

        chunks: list[Chunk] = []
        section_path: list[str] = []
        buffer = ""
        chunk_title = ""   # title captured when buffer started
        current_title = ""  # most recent heading

        for sec in sections:
            if sec.title:
                section_path = section_path[: sec.level - 1] + [sec.title]
                current_title = sec.title

            for elem in sec.elements:
                elem_text = elem.text.strip()
                if not elem_text:
                    continue

                if not buffer:
                    chunk_title = current_title
                    if elem.is_atomic:
                        # Atomic block with no buffer → emit as standalone chunk
                        self._emit_chunk(chunks, elem_text, current_title, section_path)
                    else:
                        buffer = elem_text
                else:
                    if elem.is_atomic:
                        # Atomic block at end of buffer → try to merge, borrow context if needed
                        buffer = self._merge_atomic(buffer, elem_text, chunks, chunk_title, section_path)
                        chunk_title = current_title
                    else:
                        combined = buffer + "\n" + elem_text
                        if len(combined) <= self.chunk_size:
                            buffer = combined
                        else:
                            chunks.append(Chunk(text=buffer.strip(), title=chunk_title, section_path=list(section_path)))
                            buffer = elem_text
                            chunk_title = current_title

        if buffer.strip():
            chunks.append(Chunk(text=buffer.strip(), title=chunk_title, section_path=list(section_path)))

        # Add overlap between consecutive chunks
        if len(chunks) > 1:
            chunks = self._add_overlap(chunks)

        return chunks

    def _emit_chunk(self, chunks: list[Chunk], text: str, title: str, section_path: list[str]):
        chunks.append(Chunk(text=text, title=title, section_path=list(section_path)))

    def _merge_atomic(
        self, buffer: str, atomic: str, chunks: list[Chunk],
        title: str, section_path: list[str],
    ) -> str:
        """Try to merge atomic block into buffer; if buffer is too large, borrow last N sentences as context."""
        combined = buffer + "\n" + atomic
        if len(combined) <= self.chunk_size:
            return combined

        borrow_threshold = int(self.chunk_size * self.borrow_ratio)
        if len(buffer) > borrow_threshold:
            overlap_text = self._extract_last_sentences(buffer, self.overlap_sentences)
            trimmed = buffer[: -len(overlap_text)].strip() if overlap_text else buffer
            if trimmed:
                chunks.append(Chunk(text=trimmed, title=title, section_path=list(section_path)))
            return overlap_text + "\n" + atomic if overlap_text else atomic
        else:
            chunks.append(Chunk(text=buffer.strip(), title=title, section_path=list(section_path)))
            return atomic

    def _add_overlap(self, chunks: list[Chunk]) -> list[Chunk]:
        """Prepend last N sentences of previous chunk to current chunk for context continuity."""
        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_text = chunks[i - 1].text
            overlap = self._extract_last_sentences(prev_text, self.overlap_sentences)
            if overlap:
                chunks[i].text = overlap + "\n" + chunks[i].text
            result.append(chunks[i])
        return result

    def _extract_last_sentences(self, text: str, n: int) -> str:
        """Return the last N sentences of text by splitting on sentence-ending punctuation."""
        sentences = re.split(r"(?<=[。！？.!?\n])\s*", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) <= n:
            return text
        tail = "\n".join(sentences[-n:])
        return tail if tail.strip() else ""

    # ── Recursive split (fallback for oversized blocks) ──────────────────────

    def _recursive_split(self, text: str, title: str, section_path: list[str]) -> list[Chunk]:
        """Recursively split text using heading → paragraph → line → sentence priority."""
        if len(text) <= self.chunk_size:
            return [Chunk(text=text, title=title, section_path=list(section_path))]

        for split_fn in [self._split_by_headings, self._split_by_paragraphs,
                         self._split_by_lines, self._split_by_sentences]:
            parts = split_fn(text)
            if len(parts) > 1:
                result = []
                for p in parts:
                    result.extend(self._recursive_split(p, title, section_path))
                merged = self._merge_small(result)
                return merged

        return self._split_by_char_boundary(text, title, section_path)

    def _merge_small(self, chunks: list[Chunk]) -> list[Chunk]:
        """Greedily merge adjacent small chunks back up to chunk_size."""
        merged = []
        buf_chunk = None
        for c in chunks:
            if buf_chunk is None:
                buf_chunk = c
            elif len(buf_chunk.text) + 1 + len(c.text) <= self.chunk_size:
                buf_chunk.text += "\n" + c.text
            else:
                merged.append(buf_chunk)
                buf_chunk = c
        if buf_chunk is not None:
            merged.append(buf_chunk)
        return merged

    def _split_by_headings(self, text: str) -> list[str]:
        """Split at Markdown headings (##, etc.)."""
        parts = re.split(r"(?=\n#{1,6}\s)", text)
        return [p.strip() for p in parts if p.strip()]

    def _split_by_paragraphs(self, text: str) -> list[str]:
        """Split at double newlines."""
        parts = re.split(r"\n{2,}", text)
        return [p.strip() for p in parts if p.strip()]

    def _split_by_lines(self, text: str) -> list[str]:
        """Split at single newlines."""
        parts = text.split("\n")
        return [p.strip() for p in parts if p.strip()]

    def _split_by_sentences(self, text: str) -> list[str]:
        """Split at sentence-ending punctuation."""
        parts = re.split(r"(?<=[。！？.!?\n])\s*", text)
        return [p.strip() for p in parts if p.strip()]

    def _split_by_char_boundary(self, text: str, title: str, section_path: list[str]) -> list[Chunk]:
        """Last resort: split at a preferred break point near chunk_size."""
        end = self._find_break_point(text, self.chunk_size)
        first = text[:end].strip()
        rest = text[end:].strip()
        result = []
        if first:
            result.append(Chunk(text=first, title=title, section_path=list(section_path)))
        if rest:
            result.extend(self._recursive_split(rest, title, section_path))
        return result

    def _find_break_point(self, text: str, limit: int) -> int:
        """Find best split position near limit: prefers newline > punctuation > comma > space."""
        if limit >= len(text):
            return len(text)
        candidates = [
            (r"\n", 80),
            (r"[。！？.!?]", 90),
            (r"[，、,]", 95),
            (r"\s", 100),
        ]
        search_start = max(0, limit - 100)
        best = limit
        best_priority = 999
        for pattern, priority in candidates:
            for m in re.finditer(pattern, text[search_start:limit]):
                pos = search_start + m.end()
                if pos > 0 and priority < best_priority:
                    best = pos
                    best_priority = priority
        return best


text_chunker = TextChunker()
