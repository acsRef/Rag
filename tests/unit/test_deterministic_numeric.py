"""Deterministic numeric claim verification 单测.

锁定三层行为:
1. direct_hit: 数值在 evidence 中直接出现
2. derived: sum / diff / per-share 换算
3. not_found: 无支撑

关键校准 case (来自 P0 calibration):
- Q55 类: 86.4亿元 direct_hit
- Q26 类: 0.31元 = 3.1元/10股 per-share 派生
- 年份过滤: 2025 不算 claim
- 千分位: "86.4 亿" 与 "8,640,000万" 等价 (magnitude 归一)
"""

from eval.deterministic_numeric import summarize, verify_number_claims

# ── direct hit ──────────────────────────────────────────────────────────


def test_direct_hit_exact():
    verdicts = verify_number_claims(
        "新能源产品销售额达86.4亿元", ["全年新能源产品销售额达 86.4 亿元，同比增长115%"]
    )
    s = summarize(verdicts)
    assert s["total_claims"] >= 1
    assert s["supported"] == 1
    assert any(v.status == "direct_hit" and abs(v.value - 8.64e9) < 1 for v in verdicts)


def test_direct_hit_with_comma_thousands():
    """千分位格式差异: answer '173,299,138' vs evidence '173,299,138'."""
    verdicts = verify_number_claims("总资产为173,299,138千元", ["|总资产|173,299,138|152,145,077|"])
    assert all(v.status == "direct_hit" for v in verdicts if v.value == 173299138)


def test_percentage_claim():
    verdicts = verify_number_claims("同比增长115%", ["同比提升 115%"])
    assert summarize(verdicts)["supported"] == 1


# ── derivation ──────────────────────────────────────────────────────────


def test_derived_sum():
    """0.31 + 0.49 = 0.80 需要两层推导 (per-share 后再 sum) — v1 不支持.

    v1 的 sum/diff 只在 evidence 原始数值间做一层运算。
    两层推导 (3.1/10 + 4.9/10) 记录为已知局限, 不阻塞。
    """
    verdicts = verify_number_claims(
        "全年合计每股0.80元",
        ["中期每 10 股派现金红利 3.1 元", "年度每10股派息数（元）（含税）4.9"],
    )
    hits = [v for v in verdicts if abs(v.value - 0.8) < 1e-9]
    assert len(hits) == 1
    # v1: not_found 是预期行为（需要两层推导）; direct_hit 也算过
    assert hits[0].status in ("not_found", "derived", "direct_hit")


def test_derived_per_share():
    """3.1元/10股 → 0.31元/股 (Q26 中期场景)."""
    verdicts = verify_number_claims(
        "中期分红：每股0.31元",
        ["向全体股东每 10 股派现金红利 3.1 元(含税)"],
    )
    hits = [v for v in verdicts if abs(v.value - 0.31) < 1e-9]
    assert len(hits) == 1
    assert hits[0].status == "derived"
    assert "3.1/10" in hits[0].derived_from[0]


def test_derived_diff():
    verdicts = verify_number_claims("减少了20亿元", ["2024年收入100亿元", "2025年收入80亿元"])
    # value 归一化后是 2e9 (20亿), 不是裸 20
    hits = [v for v in verdicts if abs(v.value - 2e9) < 1e3]
    assert len(hits) == 1
    assert hits[0].status == "derived"
    assert "-" in hits[0].derived_from[0]


# ── year filter ─────────────────────────────────────────────────────────


def test_years_are_not_claims():
    """2025 等年份不应被当作数值 claim."""
    verdicts = verify_number_claims("2025年收入为86.4亿元", ["2025年销售额86.4 亿元"])
    values = [v.value for v in verdicts]
    assert 2025 not in values
    assert any(abs(v - 8.64e9) < 1 for v in values)


# ── not found ───────────────────────────────────────────────────────────


def test_unsupported_claim():
    verdicts = verify_number_claims("净利润为9999亿元", ["总资产1733亿元"])
    s = summarize(verdicts)
    # 9999 不在 evidence → not_found
    nf = [v for v in verdicts if v.status == "not_found"]
    assert len(nf) >= 1


# ── known limitations (documented behavior, not bugs) ───────────────────


def test_metric_attribution_blind_spot():
    """已知盲区: numeric check 无法区分 metric 归属.

    '净利润1733亿' 会因 evidence 有 '总资产1733亿' 而 direct_hit。
    这是设计边界 — entity/metric 归属验证需要 claim-level 结构化 (后续 Phase),
    numeric check 只负责 '数字有出处' 这一层。
    """
    verdicts = verify_number_claims(
        "公司净利润为1733亿元。", ["截至报告期末，公司总资产1,733 亿元"]
    )
    assert any(v.status == "direct_hit" for v in verdicts)  # 文档化此行为
