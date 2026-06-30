"""Structure-aware semantic chunker.

Chunking strategy: each H3 section or standalone H2 section becomes one chunk.
Preamble (sections before the first H3) is merged into a single chunk.
Pure-heading sections (no content) are skipped — they only contribute to the section path.
Atomic blocks (code, table, image) naturally stay within their parent section.

Fallback: if a single section's content exceeds max_chunk_size, hard-split is used.
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
    """Splits structured sections into semantic chunks by heading boundaries."""

    def __init__(
        self,
        chunk_size: int = 512,
        max_atomic: int = 1024,
        max_chunk_size: int | None = None,
    ):
        if chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        if max_atomic < 1:
            raise ValueError("max_atomic must be >= 1")
        self.chunk_size = chunk_size
        self.max_atomic = max_atomic
        self.max_chunk_size = max_chunk_size or settings.chunk_max_size

    def chunk(self, sections: list) -> list[Chunk]:
        """Main entry point: chunk by section boundaries."""
        if not sections:
            return []
        return self._chunk_by_sections(sections)

    # ── Semantic section‑boundary chunking ─────────────────────────────────

    def _chunk_by_sections(self, sections: list) -> list[Chunk]:
        chunks: list[Chunk] = []
        path_stack: list[tuple[int, str]] = []
        preamble_elems: list = []
        preamble_path: list[str] = []

        first_h3_idx = self._find_first_h3(sections)

        for i, sec in enumerate(sections):
            level, title = sec.level, sec.title

            while path_stack and path_stack[-1][0] >= level:
                path_stack.pop()
            path_stack.append((level, title))

            content_elems = [e for e in sec.elements if e.type != "heading"]
            if not content_elems:
                continue

            section_path = [t for _, t in path_stack]

            if i < first_h3_idx:
                preamble_elems.extend(sec.elements)
                preamble_path = list(section_path)
                continue

            if level == 2 and self._has_h3_child(sections, i):
                continue

            chunk_text = self._build_chunk_text(sec.elements, section_path)
            title_val = section_path[-1]

            if len(chunk_text) > self.max_chunk_size:
                chunks.extend(self._hard_split(chunk_text, title_val, section_path))
            else:
                chunks.append(
                    Chunk(text=chunk_text, title=title_val, section_path=list(section_path))
                )

        if preamble_elems:
            chunk_text = self._build_chunk_text(preamble_elems, preamble_path)
            title_val = preamble_path[0] if preamble_path else ""
            chunks.insert(
                0,
                Chunk(text=chunk_text, title=title_val, section_path=list(preamble_path)),
            )

        return chunks

    @staticmethod
    def _find_first_h3(sections: list) -> int:
        for i, sec in enumerate(sections):
            if sec.level >= 3 and any(e.type != "heading" for e in sec.elements):
                return i
        return len(sections)

    @staticmethod
    def _has_h3_child(sections: list, idx: int) -> bool:
        for j in range(idx + 1, len(sections)):
            sec = sections[j]
            if any(e.type != "heading" for e in sec.elements):
                return sec.level == 3
        return False

    @staticmethod
    def _build_chunk_text(elements: list, section_path: list[str]) -> str:
        parts = []
        if len(section_path) > 1:
            parts.append("【" + " / ".join(section_path) + "】")
        for elem in elements:
            t = (elem.text or "").strip()
            if t:
                parts.append(t)
        return "\n".join(parts)

    # ── Hard split fallback for oversized sections ─────────────────────────

    def _hard_split(self, text: str, title: str, section_path: list[str]) -> list[Chunk]:
        result = []
        if len(text) <= self.max_chunk_size:
            return [Chunk(text=text, title=title, section_path=list(section_path))]
        end = self._find_break_point(text, self.max_chunk_size)
        first = text[:end].strip()
        rest = text[end:].strip()
        if first:
            result.append(Chunk(text=first, title=title, section_path=list(section_path)))
        if rest:
            result.append(Chunk(text=rest, title=title, section_path=list(section_path)))
        return result

    def _find_break_point(self, text: str, limit: int) -> int:
        if limit >= len(text):
            return len(text)
        candidates = [
            (r"\n\n", 70),
            (r"\n", 80),
            (r"[。！？!?]", 90),
            (r"(?<!\d)\.(?!\d)", 90),
            (r"[，、,]", 95),
            (r"\s", 100),
        ]
        search_start = max(0, limit - 100)
        best = limit
        best_priority = 999
        for pattern, priority in candidates:
            for m in re.finditer(pattern, text[search_start:limit]):
                pos = search_start + m.end()
                if pos > 0 and pos < len(text):
                    next_ch = text[pos]
                    if re.match(r'[\u4e00-\u9fff\w]', next_ch) and pattern in (r"\n", r"\s"):
                        continue
                if pos > 0 and priority < best_priority:
                    best = pos
                    best_priority = priority
        return best


text_chunker = TextChunker()
