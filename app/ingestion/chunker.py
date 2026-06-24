"""Structure-aware semantic chunker.

Chunking priority: heading > paragraph > line > sentence > character boundary.
Atomic blocks (code, table, image) are never split; oversized atomic blocks
can go up to max_atomic. Overlap borrows last N sentences from previous chunk.
"""

import re
from dataclasses import dataclass, field
from app.config import settings




@dataclass
class Chunk:
    """A single document chunk with metadata to be filled later by ChunkMetadataGenerator."""
    text: str = ""
    title: str = ""
    summary: str = ""
    questions: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    content_hash: str = ""


class TextChunker:
    """Splits structured sections into chunks with atomic-block protection and overlap."""

    def __init__(
        self,
        chunk_size: int = 512,
        overlap_sentences: int = 3,
        max_atomic: int = 1024,
        max_chunk_size: int | None = None,
        borrow_ratio: float = 0.5,
    ):
        if chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        if overlap_sentences < 0:
            raise ValueError("overlap_sentences must be >= 0")
        if max_atomic < 1:
            raise ValueError("max_atomic must be >= 1")
        if not 0.0 <= borrow_ratio <= 1.0:
            raise ValueError("borrow_ratio must be between 0.0 and 1.0")
        self.chunk_size = chunk_size
        self.overlap_sentences = overlap_sentences
        self.max_atomic = max_atomic
        self.max_chunk_size = max_chunk_size or settings.chunk_max_size
        self.borrow_ratio = borrow_ratio

    def chunk(
        self,
        sections: list,
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
        chunk_title = ""
        current_title = ""

        for sec in sections:
            if sec.title:
                level = max(0, sec.level - 1) if hasattr(sec, "level") else 0
                section_path = section_path[:level] + [sec.title]
                current_title = sec.title

            for elem in sec.elements:
                elem_text = (elem.text or "").strip()
                if not elem_text:
                    continue

                if not buffer:
                    chunk_title = current_title
                    if elem.is_atomic:
                        self._emit_chunk(chunks, elem_text, current_title, section_path, is_atomic=True)
                    else:
                        buffer = elem_text
                else:
                    if elem.is_atomic:
                        buffer = self._merge_atomic(buffer, elem_text, chunks, chunk_title, section_path)
                        chunk_title = current_title
                    else:
                        combined = buffer + "\n" + elem_text
                        if len(combined) <= self.chunk_size:
                            buffer = combined
                        else:
                            self._emit_chunk(chunks, buffer.strip(), chunk_title, section_path)
                            buffer = elem_text
                            chunk_title = current_title

        if buffer.strip():
            self._emit_chunk(chunks, buffer.strip(), chunk_title, section_path)

        # Add overlap between consecutive chunks
        if len(chunks) > 1:
            chunks = self._add_overlap(chunks)

        return chunks

    def _emit_chunk(self, chunks: list[Chunk], text: str, title: str, section_path: list[str], is_atomic: bool = False):
        limit = self.max_atomic if is_atomic else self.chunk_size
        if len(text) > limit:
            chunks.extend(self._recursive_split(text, title, section_path))
        elif len(text) > self.max_chunk_size:
            chunks.extend(self._hard_split(text, title, section_path))
        else:
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
                self._emit_chunk(chunks, trimmed, title, section_path)
            return overlap_text + "\n" + atomic if overlap_text else atomic
        else:
            self._emit_chunk(chunks, buffer.strip(), title, section_path)
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
        if n <= 0 or not text:
            return ""
        sentences = re.split(r"(?<=[。！？.!?\n])\s*", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) <= n:
            return text
        tail = "\n".join(sentences[-n:])
        return tail if tail.strip() else text

    # ── Recursive split (fallback for oversized blocks) ──────────────────────

    def _recursive_split(self, text: str, title: str, section_path: list[str], _depth: int = 0) -> list[Chunk]:
        if _depth >= 32:
            if len(text) <= self.max_chunk_size:
                return [Chunk(text=text, title=title, section_path=list(section_path))]
            return self._hard_split(text, title, section_path)
        if len(text) <= self.chunk_size:
            return [Chunk(text=text, title=title, section_path=list(section_path))]

        for split_fn in [self._split_by_headings, self._split_by_paragraphs,
                         self._split_by_lines, self._split_by_sentences]:
            parts = split_fn(text)
            if len(parts) > 1:
                result = []
                for p in parts:
                    result.extend(self._recursive_split(p, title, section_path, _depth + 1))
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
        parts = re.split(r"(?=(?:^|\n)#{1,6}\s)", text)
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

    def _hard_split(self, text: str, title: str, section_path: list[str]) -> list[Chunk]:
        """Force-split at max_chunk_size boundary with character-level fallback."""
        end = self._find_break_point(text, self.max_chunk_size)
        first = text[:end].strip()
        rest = text[end:].strip()
        result = []
        if first:
            result.append(Chunk(text=first, title=title, section_path=list(section_path)))
        if rest:
            result.extend(self._recursive_split(rest, title, section_path, 0))
        return result

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
