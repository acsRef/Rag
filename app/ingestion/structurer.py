"""
Document structure analyzer.
Transforms cleaned text into a structured tree of sections and elements,
marking code blocks, tables, and images as atomic (unsplittable).
"""
import re
from dataclasses import dataclass, field


@dataclass
class Element:
    type: str          # heading / paragraph / list / code / table / image
    text: str
    level: int = 0     # heading level (1-6), 0 for non-heading
    is_atomic: bool = False  # True for code/table/image: chunker must NOT split these


@dataclass
class StructuredSection:
    title: str = ""
    level: int = 0
    elements: list[Element] = field(default_factory=list)


# 标题黑名单:这些是 PDF 头部/末尾的"非真正章节",遇到就跳过
# (并连同后面跟着的"声明段落"一起丢弃,直到下一个真正的标题)
_TITLE_BLACKLIST = (
    "注意事项", "前言", "目录", "公告", "声明",
    "编者按", "版权", "本报告以", "本刊以", "免责声明",
)


def _is_blacklist_title(title: str) -> bool:
    return any(black in title for black in _TITLE_BLACKLIST)


class DocumentStructurer:
    def structure(self, text: str) -> list[StructuredSection]:
        sections: list[StructuredSection] = []
        current_section = StructuredSection()
        lines = text.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                i += 1
                continue

            # New section on heading
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2)
                # 标题黑名单:跳过该标题 + 后面到下一个真标题之间的"声明段落"
                if _is_blacklist_title(title):
                    i += 1  # 跳过标题行
                    while i < len(lines):
                        nxt = lines[i].strip()
                        if re.match(r"^#{1,6}\s+", nxt):
                            break
                        i += 1
                    continue
                if current_section.elements:
                    sections.append(current_section)
                current_section = StructuredSection(title=title, level=level)
                current_section.elements.append(Element("heading", stripped, level=level))
                i += 1
                continue

            # Code block: mark as atomic (must not be split across chunks)
            if stripped.startswith("```"):
                end = self._find_block_end(lines, i, "```")
                code_text = "\n".join(lines[i:end + 1])
                current_section.elements.append(Element("code", code_text, is_atomic=True))
                i = end + 1
                continue

            # Table: detect by header-separator pattern, mark as atomic
            if "|" in stripped and i + 1 < len(lines) and re.match(r"^[\s|:\-]+$", lines[i + 1]):
                end = self._find_table_end(lines, i)
                table_text = "\n".join(lines[i:end + 1])
                current_section.elements.append(Element("table", table_text, is_atomic=True))
                i = end + 1
                continue

            # Image reference: atomic
            if re.search(r"!\[.*?\]\(.*?\)", stripped):
                current_section.elements.append(Element("image", stripped, is_atomic=True))
                i += 1
                continue

            # List: collect consecutive list items
            if re.match(r"^(\s*[-*+]\s|\s*\d+[.)]\s)", stripped):
                end = self._find_list_end(lines, i)
                list_text = "\n".join(lines[i:end + 1])
                current_section.elements.append(Element("list", list_text))
                i = end + 1
                continue

            # Paragraph: accumulate consecutive non-empty, non-special lines
            para_lines = [stripped]
            j = i + 1
            while j < len(lines):
                next_stripped = lines[j].strip()
                if not next_stripped:
                    break
                if re.match(r"^(#{1,6}\s|```|!\[.*?\]\(.*?\)|\s*[-*+]\s|\s*\d+[.)]\s)", next_stripped):
                    break
                if "|" in next_stripped and j + 1 < len(lines) and re.match(r"^[\s|:\-]+$", lines[j + 1]):
                    break
                para_lines.append(next_stripped)
                j += 1
            current_section.elements.append(Element("paragraph", "\n".join(para_lines)))
            i = j

        if current_section.elements:
            sections.append(current_section)
        return sections

    def _find_block_end(self, lines: list[str], start: int, marker: str) -> int:
        for i in range(start + 1, len(lines)):
            if lines[i].strip().startswith(marker):
                return i
        return len(lines) - 1

    def _find_table_end(self, lines: list[str], start: int) -> int:
        j = start + 2
        while j < len(lines) and "|" in lines[j] and not re.match(r"^\s*#", lines[j]):
            j += 1
        return j - 1

    def _find_list_end(self, lines: list[str], start: int) -> int:
        j = start + 1
        while j < len(lines):
            stripped = lines[j].strip()
            if not stripped:
                break
            if not re.match(r"^(\s*[-*+]\s|\s*\d+[.)]\s)", stripped):
                break
            j += 1
        return j - 1


document_structurer = DocumentStructurer()
