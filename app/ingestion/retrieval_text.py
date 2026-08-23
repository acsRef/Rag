"""build_retrieval_text() — 表格感知的检索表示构造（Issue #2 第一期）。

与 build_embedding_text()（metadata prefix builder）平级、各司其职：
- build_embedding_text：文档/章节前缀增强 —— 保留供 ablation，生产不启用
- build_retrieval_text：表格块行展开归一化 —— 本模块

双表示架构（spec §5.1）：
    chunks.text           原始 Markdown（generation 通道，结构无损）
    chunks.embedding_text 本函数输出（retrieval 通道：embed 输入 + BM25 词源）

行展开格式：每行一条自包含句——「{首列}：{表头2}为{值2}，{表头3}为{值3}。」
行句子带行首实体（年份/科目），脱离表头仍可独立匹配；
不是「2024 | 150亿 | 10%」式的管道拼接。

纯字符串处理，无 LLM、无 IO；可缓存可测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SEP_ROW_RE = re.compile(r"^[\s|:\-]+$")


@dataclass
class TableBlock:
    """单个 markdown 表格块的解析结果。"""

    headers: list[str]
    rows: list[list[str]]  # 已剥管道的单元格


@dataclass
class RetrievalTextResult:
    text: str  # 归一化后的完整检索表示
    chunk_type: str | None  # 含表格块时为 "table"，否则 None
    tables: list[dict] = field(default_factory=list)  # [{"headers": [...], "data_rows": n}]


def _split_md_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _find_table_block(lines: list[str], start: int) -> tuple[int, TableBlock] | None:
    """lines[start] 起是否是一个 markdown 表格块；是则返回 (end_idx_exclusive, block)。"""
    if "|" not in lines[start]:
        return None
    if start + 1 >= len(lines) or not _SEP_ROW_RE.match(lines[start + 1]):
        return None
    headers = _split_md_row(lines[start])
    if not headers or not any(headers):
        return None
    rows: list[list[str]] = []
    j = start + 2
    while j < len(lines):
        ln = lines[j]
        if "|" not in ln:
            break
        stripped = ln.strip()
        if not stripped.startswith("|"):
            break
        rows.append(_split_md_row(ln))
        j += 1
    return j, TableBlock(headers=headers, rows=rows)


def _render_table(block: TableBlock) -> tuple[str, dict]:
    """表格 → 「表头行 + 每行自包含句」。返回 (text, meta)。"""
    out = ["| " + " | ".join(block.headers) + " |"]
    for row in block.rows:
        entity = row[0] if row else ""
        if not entity:
            out.append(" ".join(c for c in row if c))
            continue
        pairs = [
            f"{block.headers[k]}为{row[k]}"
            for k in range(1, min(len(row), len(block.headers)))
            if k < len(row) and row[k]
        ]
        out.append(f"{entity}：{'，'.join(pairs)}。" if pairs else entity)
    meta = {"headers": block.headers, "data_rows": len(block.rows)}
    return "\n".join(out), meta


def build_retrieval_text(chunk_text: str) -> RetrievalTextResult:
    """扫描 chunk 文本中的 markdown 表格块并归一化；其余文本原样保留。"""
    if not chunk_text or "|" not in chunk_text:
        return RetrievalTextResult(text=chunk_text or "", chunk_type=None, tables=[])

    lines = chunk_text.split("\n")
    out_lines: list[str] = []
    tables: list[dict] = []
    i = 0
    while i < len(lines):
        found = _find_table_block(lines, i)
        if found is None:
            out_lines.append(lines[i])
            i += 1
            continue
        end, block = found
        rendered, meta = _render_table(block)
        out_lines.append(rendered)
        tables.append(meta)
        i = end
    return RetrievalTextResult(
        text="\n".join(out_lines),
        # 表格块判定：要求至少一行数据（避免 2 行 stub 假阳性 —— 仅 header+separator 的 chunk 不算表格）
        chunk_type="table" if any(t["data_rows"] > 0 for t in tables) else None,
        tables=tables,
    )
