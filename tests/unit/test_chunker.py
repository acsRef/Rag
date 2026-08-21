"""chunker 尺寸约束测试：无 H3 不坍缩、超限迭代切分、原子块保护、重叠窗口。"""

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


def test_oversized_section_packs_on_element_boundaries():
    """超长 section 优先按元素边界装箱：表格（atomic）不被拦腰切断。

    旧实现对拼接后的整段文本硬切，恰好落在表格中间时行数据被拆散，
    embedding 信号与检索可读性双输。
    """
    table = NL.join(
        ["| 项目 | 数值 |", "|---|---|"] + ["| 行%d | %d |" % (i, i) for i in range(30)]
    )
    md = NL.join(
        [
            "# T",
            "### 混合小节",
            "第一段普通文本。" * 20,
            table,
            "第二段普通文本。" * 20,
        ]
    )
    sections = document_structurer.structure(md)
    chunks = TextChunker(max_chunk_size=400).chunk(sections)
    assert len(chunks) > 1
    assert all(len(c.text) <= 400 for c in chunks)
    # 表格行要么整体在某个 chunk 内，要么整体缺席；不允许半张表
    rows = ["| 行%d | %d |" % (i, i) for i in range(30)]
    for row in rows:
        hits = [c for c in chunks if row in c.text]
        assert len(hits) <= 1, "表格行被拆进了多个 chunk"


def test_hard_split_keeps_overlap_between_fragments():
    """单元素超长走文本硬切时，相邻片段带重叠窗口（README 宣称的行为）。

    文本用位置敏感的数字序列，重叠与否可精确判定（纯重复字符会假通过）。
    """
    chunker = TextChunker(max_chunk_size=200)
    text = "".join("%04d" % (i % 10000) for i in range(250))  # 1000 字符
    fragments = chunker._hard_split(text, "标题", ["标题"])
    assert len(fragments) >= 5
    assert all(len(f.text) <= 200 for f in fragments)
    overlap = TextChunker._SPLIT_OVERLAP
    for prev, nxt in zip(fragments, fragments[1:]):
        # 下一片段以「上一片段完整尾部」开头 = 真重叠（位置敏感文本可精确判定）
        assert nxt.text.startswith(prev.text[-overlap:]), "相邻硬切片段缺少重叠上下文"
