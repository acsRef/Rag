"""build_embedding_text() — 纯函数，把 chunk + document 元数据拼成 embedding 输入。

把 doc.title / section_path / table_title / figure_title 加到正文前面，让
embedding 知道"这段话来自哪份文档哪个章节"——对 C 类跨文档检索 / E 类时序题
都有用（query 提到"2024年报"时 embedding 能匹配到正确 section）。

设计原则：
- 顺序：文档 > 章节 > 表格 > 图表 > 正文
- 缺字段就跳过该行（不留空 "文档：")
- 不调 LLM，纯字符串拼装；可缓存可测试
- 不含 summary / questions / search_text（避免 LLM 总结污染 embedding）

EMBEDDING_TEXT_VERSION = 2 跟 db.embedding_version 对齐——所有用 build_embedding_text
重 embed 的 chunk 都标 version=2，hybrid_search 加 AND embedding_version = 2 过滤
隔离新旧 embedding。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.store.db import Chunk, Document


EMBEDDING_TEXT_VERSION = 2  # 跟 chunks.embedding_version 列对齐


def build_embedding_text(chunk: Chunk, doc: Document) -> str:
    """构造 chunk 的 embedding 输入字符串。

    Args:
        chunk: 已 enriched 的 Chunk 对象（含 section_path / table_title / figure_title）
        doc: Chunk 所属的 Document（含 title）

    Returns:
        "\n" 分隔的多行字符串；text 为空且无任何元数据时返回 ""
    """
    parts: list[str] = []

    # 1) 文档级（Document.filename）— Document 没有 title 字段，用 filename（含年份/类型）作为
    # 文档标识；query 提到"2024年报"时 embedding 能匹配到正确文档。
    doc_filename = getattr(doc, "filename", None) or ""
    if doc_filename:
        parts.append(f"文档：{doc_filename}")

    # 2) 章节级（Chunk.section_path）
    section_path = getattr(chunk, "section_path", None) or ""
    if section_path:
        parts.append(f"章节：{section_path}")

    # 3) 表格（Chunk.table_title，可能为空）
    table_title = getattr(chunk, "table_title", None) or ""
    if table_title:
        parts.append(f"表格：{table_title}")

    # 4) 图表（Chunk.figure_title，可能为空）
    figure_title = getattr(chunk, "figure_title", None) or ""
    if figure_title:
        parts.append(f"图表：{figure_title}")

    # 5) 正文（必填；非空才加）
    text = getattr(chunk, "text", None) or ""
    if not text:
        # 正文为空 = 无效 chunk；不浪费 embedding 维度在孤立的元数据上
        return ""

    parts.append(f"正文：{text}")
    return "\n".join(parts)
