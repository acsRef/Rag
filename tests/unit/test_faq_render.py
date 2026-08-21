"""FAQ 文档渲染：唯一写入格式 + 幂等文件名。"""

from mcp_server.render import faq_filename, render_faq_doc


def test_faq_filename_idempotent():
    assert faq_filename("faq-001") == "faq-faq-001.md"
    assert faq_filename("退货率") == "faq-退货率.md"


def test_render_faq_doc_full():
    md = render_faq_doc(
        question="各区域销售额排名",
        keywords=["区域", "销售额", "排名", "销售"],
        tables=["fact_sales", "dim_region"],
        sql="SELECT r.region_name AS 区域, SUM(f.total_amount) AS 销售额\nFROM fact_sales f JOIN dim_region r ON f.region_id = r.region_id\nGROUP BY r.region_name\nORDER BY 销售额 DESC",
        note="区域销售额对 fact_sales.total_amount 求和；ORDER BY 聚合别名降序即为排名。",
    )
    assert md.startswith("# 各区域销售额排名")
    assert "关键词: 区域、销售额、排名、销售" in md
    assert "涉及表: fact_sales, dim_region" in md
    assert "示例 SQL:" in md
    assert "```sql" in md and "SELECT r.region_name" in md
    assert "要点:" in md
    assert "ORDER BY 聚合别名降序" in md


def test_render_faq_doc_minimal():
    md = render_faq_doc(question="毛利", keywords=[], tables=[], sql="SELECT 1", note="")
    assert md.startswith("# 毛利")
    assert "关键词" not in md
    assert "涉及表" not in md
    assert "要点" not in md
    assert "SELECT 1" in md
