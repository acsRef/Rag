"""PII：中文排除词生效、ASCII 排除词保持。"""


def test_cjk_exclusion_word_skips_masking():
    from app.core.pii_scanner import scan
    # "示例"是排除词：旧实现  对 CJK 无效 → 照样命中
    findings = scan("下面是示例号码 13800138000 仅用于演示")
    assert not any(f.rule_name == "cn_phone" for f in findings)


def test_ascii_exclusion_still_works():
    from app.core.pii_scanner import scan
    findings = scan("sample number 13800138000 for demo")
    assert not any(f.rule_name == "cn_phone" for f in findings)
