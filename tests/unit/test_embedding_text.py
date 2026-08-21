"""app/ingestion/embedding_text.py 测试：build_embedding_text() 纯函数（Day 2 上午）。

设计目标：构造一个比 chunk.text 更适合 embedding 的字符串——加入 doc.title /
section_path / table_title / figure_title 等元数据前缀，让 embedding 知道这段话
来自哪份文档哪个章节。

锁定：
- 所有字段都 Optional / 空字符串时退化为只用 text
- 字段顺序：文档 > 章节 > 表格 > 图表 > 正文（与 plan §1.3 一致）
- 不调 LLM，纯字符串拼装
- 同样输入产生同样输出（frozen-style deterministic）

注：用 SimpleNamespace 模拟 Chunk/Document（避免 SQLAlchemy __init__ 限制 + 不
依赖尚未加列的字段如 table_title / figure_title）。
"""

from types import SimpleNamespace

from app.ingestion.embedding_text import build_embedding_text


def _ns(**kwargs) -> SimpleNamespace:
    """构造 SimpleNamespace chunk/doc。"""
    return SimpleNamespace(**kwargs)


def _chunk(text="正文", **kwargs) -> SimpleNamespace:
    defaults = dict(
        chunk_id="c1",
        document_id="d1",
        kb_id="kb1",
        text=text,
        embedding_text=None,
        embedding=None,
        title="",
        summary="",
        questions="",
        section_path="",
        search_text="",
        content_hash="",
        visibility="public",
        allowed_roles=[],
        created_at=None,
        # Day 2 接入后字段
        year=None,
        page_start=None,
        page_end=None,
        embedding_version=1,
        table_title="",
        figure_title="",
    )
    defaults.update(kwargs)
    return _ns(**defaults)


def _doc(**kwargs) -> SimpleNamespace:
    defaults = dict(
        document_id="d1",
        kb_id="kb1",
        filename="三一重工_2024年年度报告.pdf",
        owner_id="u1",
    )
    defaults.update(kwargs)
    return _ns(**defaults)


# ── 文档级标识：用 filename 而非 title（Document 模型无 title） ───


def test_doc_filename_used_as_doc_identifier():
    chunk = _chunk(text="X", title="chunk-title-should-not-appear")
    doc = _doc(filename="三一重工_2024年年度报告.pdf")
    out = build_embedding_text(chunk, doc)
    assert "三一重工_2024年年度报告.pdf" in out
    assert "chunk-title-should-not-appear" not in out


# ── 最简场景 ─────────────────────────────────────────────


def test_empty_chunk_text_returns_empty_string():
    """text 为空时不应抛错；返回 '' 或仅元数据前缀。"""
    chunk = _chunk(text="")
    doc = _doc()
    out = build_embedding_text(chunk, doc)
    # text 空 + 无任何元数据 → 返回 ''
    assert out == ""


def test_only_text_when_no_metadata():
    chunk = _chunk(text="营业收入 100 亿")
    doc = _doc()  # filename='三一重工_2024年年度报告.pdf'
    out = build_embedding_text(chunk, doc)
    # filename 总有值（默认 doc）；只有正文+文档标识
    assert out == "文档：三一重工_2024年年度报告.pdf\n正文：营业收入 100 亿"


def test_explicit_empty_filename_omits_doc_line():
    chunk = _chunk(text="X")
    doc = _doc(filename="")
    out = build_embedding_text(chunk, doc)
    # filename 为空 → 跳过"文档："行；只输出正文
    assert out == "正文：X"


# ── 字段顺序：文档 > 章节 > 表格 > 图表 > 正文 ─────────────


def test_field_order_doc_section_table_figure_text():
    chunk = _chunk(
        text="正文X",
        section_path="财务 > 主要会计数据",
    )
    doc = _doc(filename="三一2024年报.pdf")
    out = build_embedding_text(chunk, doc)
    # 顺序：文档 > 章节 > 正文
    lines = out.split("\n")
    assert lines[0] == "文档：三一2024年报.pdf"
    assert lines[1] == "章节：财务 > 主要会计数据"
    assert lines[-1] == "正文：正文X"


def test_table_title_included_when_set():
    """plan §1.3 表格 chunk 应有 table_title 前缀（Day 2 接入后）。"""
    chunk = _chunk(text="数据", section_path="财务 > 主要会计数据", table_title="主要会计数据表")
    doc = _doc(filename="doc.pdf")
    out = build_embedding_text(chunk, doc)
    assert "表格：主要会计数据表" in out
    assert out.index("文档：") < out.index("章节：") < out.index("表格：") < out.index("正文：")


def test_figure_title_included_when_set():
    chunk = _chunk(text="数据", figure_title="营收趋势图")
    doc = _doc(filename="doc.pdf")
    out = build_embedding_text(chunk, doc)
    assert "图表：营收趋势图" in out


def test_no_table_or_figure_when_not_set():
    chunk = _chunk(text="数据")
    doc = _doc()
    out = build_embedding_text(chunk, doc)
    assert "表格：" not in out
    assert "图表：" not in out


# ── Deterministic ───────────────────────────────────────


def test_same_input_same_output():
    chunk = _chunk(text="X", section_path="s1")
    doc = _doc(filename="t.pdf")
    out1 = build_embedding_text(chunk, doc)
    out2 = build_embedding_text(chunk, doc)
    assert out1 == out2


def test_embedding_text_does_not_include_summary_or_questions():
    """summary/questions 不参与（避免 embedding 被 LLM 总结污染）。"""
    chunk = _chunk(text="X", summary="一段总结", questions="Q1; Q2")
    doc = _doc(filename="d.pdf")
    out = build_embedding_text(chunk, doc)
    assert "一段总结" not in out
    assert "Q1" not in out
