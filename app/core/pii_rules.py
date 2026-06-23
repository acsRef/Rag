"""Seed PII rules + startup sync to DB."""

import re


DEFAULT_RULES = [
    {
        "rule_name": "cn_id_card",
        "display_name": "身份证号",
        "pattern": r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
        "validation_fn": "validate_id_card",
        "strategy": "mask",
        "mask_mode": "partial",
        "exclusion_words": "示例;例如;举例;模板;demo;test;测试;sample",
        "description": "18 位公民身份证号码（含校验位）",
        "is_active": True,
    },
    {
        "rule_name": "cn_phone",
        "display_name": "手机号",
        "pattern": r"\b1[3-9]\d{9}\b",
        "validation_fn": "validate_phone",
        "strategy": "mask",
        "mask_mode": "partial",
        "exclusion_words": "示例;例如;举例;模板;demo;test;测试;sample",
        "description": "中国大陆手机号码（11 位）",
        "is_active": True,
    },
    {
        "rule_name": "email",
        "display_name": "电子邮箱",
        "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "validation_fn": "validate_email",
        "strategy": "mask",
        "mask_mode": "partial",
        "exclusion_words": "示例;例如;举例;模板;demo;test;测试;sample",
        "description": "电子邮箱地址",
        "is_active": True,
    },
    {
        "rule_name": "cn_bank_card",
        "display_name": "银行卡号",
        "pattern": r"\b[1-5]\d{14,18}\b",
        "validation_fn": "validate_bank_card",
        "strategy": "mask",
        "mask_mode": "partial",
        "exclusion_words": "示例;例如;举例;模板;demo;test;测试;sample",
        "description": "银行卡号（Luhn 校验）",
        "is_active": True,
    },
    {
        "rule_name": "cn_passport",
        "display_name": "护照号",
        "pattern": r"\b[1-9]\d{8}\b",
        "validation_fn": "validate_passport",
        "strategy": "mask",
        "mask_mode": "partial",
        "exclusion_words": "",
        "description": "中国因私普通护照号码（9 位数字）",
        "is_active": False,
    },
]


def validate_id_card(value: str) -> bool:
    """Validate Chinese ID card number using weighted sum modulo 11."""
    if len(value) != 18:
        return False
    if not value[:17].isdigit():
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = "10X98765432"
    total = sum(int(value[i]) * weights[i] for i in range(17))
    return value[17].upper() == check_chars[total % 11]


def validate_phone(value: str) -> bool:
    """Validate Chinese phone number by first two digits."""
    return len(value) == 11 and value[:2] in (
        "13", "14", "15", "16", "17", "18", "19",
    )


def validate_email(value: str) -> bool:
    """Basic email format validation (pattern already covers this)."""
    return "@" in value and "." in value.split("@")[-1]


def validate_bank_card(value: str) -> bool:
    """Validate bank card number using Luhn algorithm."""
    if len(value) < 15 or len(value) > 19:
        return False
    if not value.isdigit():
        return False
    total = 0
    for i, digit in enumerate(reversed(value)):
        n = int(digit)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def validate_passport(value: str) -> bool:
    """Validate Chinese passport: 9 digits, not starting with 0."""
    return value.isdigit() and value[0] != "0" and len(value) == 9


VALIDATORS = {
    "validate_id_card": validate_id_card,
    "validate_phone": validate_phone,
    "validate_email": validate_email,
    "validate_bank_card": validate_bank_card,
    "validate_passport": validate_passport,
}


def seed_pii_rules():
    """Sync DEFAULT_RULES to sensitive_rules table on startup."""
    from app.store.db import get_session, SensitiveRule, utc_now
    session = get_session()
    try:
        for rule in DEFAULT_RULES:
            existing = session.query(SensitiveRule).filter(
                SensitiveRule.rule_name == rule["rule_name"]
            ).first()
            if existing:
                continue
            session.add(SensitiveRule(
                rule_name=rule["rule_name"],
                display_name=rule["display_name"],
                pattern=rule["pattern"],
                validation_fn=rule["validation_fn"],
                strategy=rule["strategy"],
                mask_mode=rule["mask_mode"],
                exclusion_words=rule["exclusion_words"],
                description=rule.get("description", ""),
                is_active=rule["is_active"],
                created_at=utc_now(),
            ))
        session.commit()
    finally:
        session.close()
