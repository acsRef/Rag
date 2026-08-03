"""chunker 尺寸约束测试：无 H3 不坍缩、超限迭代切分。"""
from app.ingestion.chunker import TextChunker
from app.ingestion.structurer import document_structurer

NL = chr(10)


def test_no_h3_long_doc_split_by_size():
    text = "# 大标题" + NL + NL + ("这是一段没有任何小节的长文本。" * 30 + NL + NL) * 3
    sections = document_structurer.structure(text)
    chunks = TextChunker(max_chunk_size=300).chunk(sections)
    assert len(chunks) > 1, "无 H3 文档坍缩成了单 chunk"
    assert all(len(c.text) <= 300 for c in chunks)


def test_hard_split_recursive_on_oversized_section():
    md = NL.join(["# T", "## 章节", "### 小节", "长" * 1000])
    sections = document_structurer.structure(md)
    chunks = TextChunker(max_chunk_size=200).chunk(sections)
    assert len(chunks) >= 5
    assert all(len(c.text) <= 200 for c in chunks)
