"""PII P0：银联卡检测（6 开头卡段）+ 多规则重叠命中掩码去重。"""

from app.core.pii_rules import validate_bank_card
from app.core.pii_scanner import _partial_mask, mask_text, scan


def _luhn_check_digit(partial: str) -> str:
    """计算使 partial+digit 通过 Luhn 的校验位。"""
    total = 0
    for i, ch in enumerate(reversed(partial)):
        d = int(ch)
        if i % 2 == 0:  # 完整卡号中奇数位（校验位为第 0 位）
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


UNIONPAY_PARTIAL = "621700000000000"  # 15 位基数
UNIONPAY = UNIONPAY_PARTIAL + _luhn_check_digit(UNIONPAY_PARTIAL)  # 16 位 62 开头


def test_unionpay_number_is_luhn_valid():
    assert UNIONPAY.startswith("62") and len(UNIONPAY) == 16
    assert validate_bank_card(UNIONPAY)


def test_unionpay_detected_and_masked():
    text = "付款卡号 %s 请核对" % UNIONPAY
    findings = scan(text)
    rules = {f.rule_name for f in findings}
    assert "cn_bank_card_unionpay" in rules
    masked = mask_text(text, findings=findings)
    assert UNIONPAY not in masked
    assert _partial_mask(UNIONPAY) in masked


def _id_card_also_luhn_valid() -> str:
    """穷举出一个同时过 mod-11（身份证）与 Luhn（银行卡）的 18 位数，
    用于复现 id_card + bank_card 双规则重叠命中。"""
    from app.core.pii_rules import validate_id_card

    # 17 位本体 = 110105（地区）+ 19491231（生日）+ 3 位顺序码，末位为校验位
    for tail3 in range(1000):
        body = "11010519491231%03d" % tail3
        for check in "0123456789X":
            candidate = body + check
            if validate_id_card(candidate) and validate_bank_card(candidate):
                return candidate
    raise AssertionError("未找到双规则命中的测试号码")


def test_overlapping_findings_masked_once():
    number = _id_card_also_luhn_valid()
    text = "证件号 %s 存档" % number
    findings = scan(text)
    assert len(findings) >= 2  # 确实被两条规则同时命中
    masked = mask_text(text, findings=findings)
    assert number not in masked
    # 首个区间整体生效、无偏移错乱：结果恰为一次 partial mask
    assert masked == "证件号 %s 存档" % _partial_mask(number)
