"""app/core/query_parser.py 测试：parse_query() + ParsedQuery（Day 1 晚上）。

锁定：
- 年份提取：4 位数字限定 2020-2029（避免 "4,527" / "P5" 等误匹配）
- 多年度范围："2023-2025年" → [2023, 2024, 2025]；"2023、2024年" → [2023, 2024]
- "近三年" 语义：用 corpus_years 参数（默认 None 时返回空集）
- 指标关键词：营业收入 / 净利润 / 归母净利润 / 研发投入 / 员工 / 专利 / 分红 等
- ParsedQuery.filters 自动从 years/document_ids 构造 RetrievalFilter
"""

from app.core.query_parser import parse_query

# ── 年份提取 ──────────────────────────────────────────────


def test_extract_single_year():
    p = parse_query("三一重工2023年的营业收入是多少")
    assert p.years == [2023]


def test_extract_year_in_middle_of_query():
    p = parse_query("2024年三一重工研发投入")
    assert p.years == [2024]


def test_extract_multiple_years_with_comma():
    p = parse_query("对比 2023、2024 年的研发投入")
    assert p.years == [2023, 2024]


def test_extract_multiple_years_in_range():
    p = parse_query("对比 2023-2025 年的营业收入")
    assert p.years == [2023, 2024, 2025]


def test_extract_range_with_short_end():
    """2023-25 = 2023, 2024, 2025（短写法扩展）"""
    p = parse_query("对比 2023-25 年的营业收入")
    assert p.years == [2023, 2024, 2025]


def test_dedup_years_preserves_order():
    p = parse_query("2024年相比2023年变化如何")
    assert p.years == [2024, 2023]


def test_filter_non_year_four_digit_numbers():
    """"4,527,498" / "P5" / "5,975" 等 4 位数字不应误判为年份。"""
    # 4,527 → 4527，不在 2020-2029 区间，忽略
    p = parse_query("归母净利润为 4,527,498 千元")
    assert p.years is None
    # "P5" / "P11" 等页码
    p = parse_query("P5 表格中的数据")
    assert p.years is None


def test_no_year_in_query():
    p = parse_query("三一重工的主营业务是什么")
    assert p.years is None


def test_corpus_years_for_recent_three_years():
    """'近三年' → corpus_years 参数决定（默认 None 返回 None）"""
    p = parse_query("三一重工近三年的营收")
    assert p.years is None
    p2 = parse_query("三一重工近三年的营收", corpus_years=[2023, 2024, 2025])
    assert p2.years == [2023, 2024, 2025]


def test_year_outside_valid_range_ignored():
    """1990 或 2099 不在 2020-2029 范围 → 忽略"""
    p = parse_query("对比 1990 年和 2099 年的数据")
    assert p.years is None


# ── 指标提取 ──────────────────────────────────────────────


def test_extract_metric_revenue():
    p = parse_query("三一重工2024年的营业收入是多少")
    assert p.intent_metric == "营业收入"


def test_extract_metric_net_profit():
    p = parse_query("2024 年归母净利润")
    assert p.intent_metric == "归母净利润"


def test_extract_metric_rnd_investment():
    p = parse_query("研发投入情况")
    assert p.intent_metric == "研发投入"


def test_extract_metric_employee():
    p = parse_query("员工人数")
    # longest-first：'员工人数' 比 '员工' 更具体
    assert p.intent_metric == "员工人数"


def test_extract_metric_patent():
    p = parse_query("专利申请数量")
    # '专利申请' 比 '专利' 更具体
    assert p.intent_metric == "专利申请"


def test_extract_metric_dividend():
    p = parse_query("现金分红方案")
    # '现金分红' / '分红方案' 都是 4 字符；先命中 '现金分红'（在表里靠前）
    assert p.intent_metric == "现金分红"


def test_no_metric_in_query():
    p = parse_query("三一重工2024年")
    assert p.intent_metric is None


def test_metric_priority_picks_longest_match():
    """'归母净利润' 与 '净利润' 都匹配 → 选更具体的（更长）。"""
    p = parse_query("归母净利润同比增长")
    # 如果 keywords 表里两个都有，应该选"归母净利润"
    assert p.intent_metric == "归母净利润"


# ── ParsedQuery 构造 ─────────────────────────────────────


def test_parsed_query_filters_built_from_years():
    p = parse_query("2023 年的营业收入")
    assert p.filters.years == frozenset({2023})


def test_parsed_query_filters_empty_when_no_signals():
    p = parse_query("三一重工的主营业务")
    assert p.filters.is_empty() is True


def test_parsed_query_filters_only_years_no_doc_ids():
    """parser 只填 years；document_ids 由 pipeline 后续解析（避免 parser 强依赖 DB）。"""
    p = parse_query("2023-2025 年的研发投入")
    assert p.filters.years == frozenset({2023, 2024, 2025})
    assert p.filters.document_ids is None


def test_parsed_query_raw_preserved():
    q = "三一重工 2024 年营收"
    p = parse_query(q)
    assert p.raw == q


def test_parsed_query_document_ids_passed_through_when_provided():
    """parser 支持外部传入已知 doc_ids（pipeline 阶段 year→doc_id 解析用）。"""
    p = parse_query("某问题", document_ids=["doc1", "doc2"])
    assert p.document_ids == ["doc1", "doc2"]
    assert p.filters.document_ids == frozenset({"doc1", "doc2"})


# ── 边界 ────────────────────────────────────────────────


def test_empty_query():
    p = parse_query("")
    assert p.years is None
    assert p.intent_metric is None
    assert p.filters.is_empty()


def test_only_punctuation():
    p = parse_query("？？？")
    assert p.years is None
    assert p.intent_metric is None


def test_unicode_garbage_safe():
    p = parse_query("🤖 𓀀 𓂀 测试")
    assert p.years is None
    assert p.intent_metric is None
