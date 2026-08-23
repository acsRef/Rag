"""表格归一化检索表示 —— 行级自包含自然语言句（spec §5.1）。"""

from app.ingestion.retrieval_text import RetrievalTextResult, build_retrieval_text

BIG_TABLE = """| 项目 | 2023年 | 2024年 |
| --- | --- | --- |
| 营业收入 | 100亿元 | 150亿元 |
| 净利润 | 10亿元 | 15亿元 |
| 归母净资产 | 500亿元 | 520亿元 |
| 经营现金流 | 80亿元 | 95亿元 |
| 研发投入 | 5亿元 | 6亿元 |
| 总资产 | 900亿元 | 950亿元 |"""


def test_plain_text_passthrough_unchanged():
    res = build_retrieval_text("这是普通段落。\n第二段。")
    assert isinstance(res, RetrievalTextResult)
    assert res.text == "这是普通段落。\n第二段。"
    assert res.chunk_type is None
    assert res.tables == []


def test_big_table_expanded_to_selfcontained_rows():
    res = build_retrieval_text(BIG_TABLE)
    assert res.chunk_type == "table"
    assert len(res.tables) == 1
    assert res.tables[0]["headers"] == ["项目", "2023年", "2024年"]
    assert res.tables[0]["data_rows"] == 6
    assert "营业收入：2023年为100亿元，2024年为150亿元。" in res.text
    assert "净利润：2023年为10亿元，2024年为15亿元。" in res.text
    assert "| 营业收入 |" not in res.text
    assert "---" not in res.text


def test_header_line_kept_for_context():
    res = build_retrieval_text(BIG_TABLE)
    first_line = res.text.split("\n")[0]
    assert "项目" in first_line and "2023年" in first_line


def test_mixed_paragraph_and_table():
    mixed = "前文说明如下：\n" + BIG_TABLE + "\n后文备注。"
    res = build_retrieval_text(mixed)
    assert res.chunk_type == "table"
    assert res.text.startswith("前文说明如下：")
    assert res.text.endswith("后文备注。")
    assert "营业收入：2023年为100亿元" in res.text


def test_small_nl_style_table_not_double_processed():
    nl = "指示灯 绿色 正常\n指示灯 红色 故障"
    res = build_retrieval_text(nl)
    assert res.chunk_type is None
    assert res.text == nl


def test_ragged_row_falls_back_to_space_join():
    ragged = """| 项目 | 2023年 | 2024年 |
| --- | --- | --- |
| 营业收入 | 100亿元 |
| 净利润 | 10亿元 | 15亿元 |
| 归母净资产 | 500亿元 | 520亿元 |
| 经营现金流 | 80亿元 | 95亿元 |
| 研发投入 | 5亿元 | 6亿元 |"""
    res = build_retrieval_text(ragged)
    assert res.chunk_type == "table"
    assert "100亿元" in res.text


def test_section_header_prefix_preserved():
    text = "【2023年 / 主要会计数据】\n" + BIG_TABLE
    res = build_retrieval_text(text)
    assert res.text.startswith("【2023年 / 主要会计数据】")


def test_deterministic_output():
    assert build_retrieval_text(BIG_TABLE).text == build_retrieval_text(BIG_TABLE).text