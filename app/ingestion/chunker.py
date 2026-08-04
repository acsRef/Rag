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


def _clean_table_text(table_text: str) -> str:
    """Strip markdown table formatting and convert to compact natural language.

    Input:
        | 指示灯 | 颜色 | 状态含义 |
        |--------|------|---------|
        | PWR | 绿色常亮 | 设备供电正常 |
        | SYS | 绿色闪烁 | 系统运行中 |

    Output:
        指示灯 PWR 颜色 绿色常亮 状态含义 设备供电正常
        指示灯 SYS 颜色 绿色闪烁 状态含义 系统运行中

    Embedding signal density improves dramatically because ~60 % of formatting
    tokens (pipes, dashes, alignment markers) are removed.
    """
    lines = [ln.strip() for ln in table_text.strip().split("\n") if ln.strip()]
    if len(lines) < 2:
        return table_text

    # Parse header row:  | col1 | col2 | col3 |
    m = re.match(r"^\|(.+)\|$", lines[0])
    if not m:
        return table_text
    headers = [h.strip() for h in m.group(1).split("|")]

    # Skip separator line (line 1), process data rows
    rows = []
    for line in lines[2:]:
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(" ".join(f"{h} {c}" for h, c in zip(headers, cells)))
        else:
            # Fallback: join cells without headers
            rows.append(" ".join(cells))
    return "\n".join(rows) if rows else table_text


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
                chunks.extend(self._pack_oversized(sec.elements, section_path, title_val))
            else:
                chunks.append(
                    Chunk(text=chunk_text, title=title_val, section_path=list(section_path))
                )

        if preamble_elems:
            chunk_text = self._build_chunk_text(preamble_elems, preamble_path)
            title_val = preamble_path[0] if preamble_path else ""
            # 无 H3 的文档全文都走 preamble 分支——同样必须受尺寸约束，
            # 旧实现直接合成单 chunk，长文档被 embedding 截断、后半段不可检索
            if len(chunk_text) > self.max_chunk_size:
                chunks[:0] = self._pack_oversized(preamble_elems, preamble_path, title_val)
            else:
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
            if not t:
                continue
            if elem.type == "table":
                t = _clean_table_text(t)
            parts.append(t)
        return "\n".join(parts)

    # ── Hard split fallback for oversized sections ─────────────────────────

    _SPLIT_OVERLAP = 64   # 相邻硬切片段的重叠窗口（保留切点上下文）

    def _pack_oversized(self, elements: list, section_path: list[str], title: str) -> list[Chunk]:
        """超长 section 的元素级装箱：按元素边界打包，atomic 块（代码/表格/图片）
        整体不拆；仅当单个元素自身超限时才退回文本级硬切。

        旧实现对整段拼接文本硬切，切点可能落在表格行中间，embedding 信号
        与检索可读性双输。
        """
        header = "【" + " / ".join(section_path) + "】\n" if len(section_path) > 1 else ""
        packed: list[Chunk] = []
        cur_parts: list[str] = []
        cur_len = len(header)

        def flush() -> None:
            nonlocal cur_parts, cur_len
            if cur_parts:
                packed.append(Chunk(
                    text=header + "\n".join(cur_parts),
                    title=title, section_path=list(section_path)))
                cur_parts = []
                cur_len = len(header)

        for elem in elements:
            # 与 _build_chunk_text 保持内容一致（heading 行同样入文）
            t = (elem.text or "").strip()
            if not t:
                continue
            if elem.type == "table":
                t = _clean_table_text(t)
            e_len = len(t) + 1
            if cur_parts and cur_len + e_len > self.max_chunk_size:
                flush()
            if e_len > self.max_chunk_size:
                # 单元素超限（巨型 atomic 块）：无法整体保留，文本级硬切
                flush()
                packed.extend(self._hard_split(t, title, section_path))
                continue
            cur_parts.append(t)
            cur_len += e_len
        flush()
        return packed

    def _hard_split(self, text: str, title: str, section_path: list[str]) -> list[Chunk]:
        """迭代切分直到所有片段 ≤ max_chunk_size。

        旧实现只切一刀：超过 2 倍上限的 section 其 rest 原样成 chunk，
        同样被 embedding 截断。相邻片段携带 _SPLIT_OVERLAP 字符重叠，
        避免切点处的语义被拦腰截断（README 宣称的「重叠窗口」）。
        """
        result: list[Chunk] = []
        pending = [text]
        prev_tail = ""
        while pending:
            piece = pending.pop(0)
            if prev_tail:
                piece = prev_tail + piece
                prev_tail = ""
            if len(piece) <= self.max_chunk_size:
                if piece.strip():
                    result.append(
                        Chunk(text=piece.strip(), title=title, section_path=list(section_path)))
                continue
            end = self._find_break_point(piece, self.max_chunk_size)
            first = piece[:end].strip()
            rest = piece[end:].strip()
            if first:
                result.append(
                    Chunk(text=first, title=title, section_path=list(section_path)))
                prev_tail = first[-self._SPLIT_OVERLAP:]
            if rest:
                pending.append(rest)
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
