"""PII scanning engine: regex → algorithm validation → context exclusion → mask/reject.

三层防御目的:
  1. 正则匹配:快速过滤大量候选(身份证 18 位/手机 11 位/邮箱/银行卡 15-19 位)
  2. 算法校验:用 validation_fn(身份证校验位/手机号段位/银行卡 Luhn)消除误报
     — 例如 `123456789012345678` 形态合法但校验位错的身份证会被剔除
  3. 上下文排除:匹配前后 20 字符内若出现 `示例/例如/test/sample` 等关键词,则跳过
     — 处理文档中"如 13800138000 是示例号码"这类非真实数据

策略:
  - mask(部分):保留首 3 末 4 位,中间 `*` 替代(手机/身份证默认)
  - mask(完全):整段替换为 `[已脱敏]`
  - reject:不入索引,PII 告警入库,管理员审核
  - audit:只告警,文本不动(预留)
"""

import logging
import re
import time
from typing import NamedTuple
from app.config import settings
from app.core.pii_rules import VALIDATORS, DEFAULT_RULES

logger = logging.getLogger(__name__)


class PiiMatch(NamedTuple):
    rule_name: str
    strategy: str
    mask_mode: str
    start: int
    end: int
    matched_text: str


_rule_cache: list[dict] | None = None
_cache_ts: float = 0


def _build_rule_dict(rule) -> dict:
    return {
        "rule_name": rule["rule_name"],
        "pattern": re.compile(rule["pattern"]) if rule.get("pattern") else None,
        "validation_fn": rule.get("validation_fn", ""),
        "strategy": rule.get("strategy", "mask"),
        "mask_mode": rule.get("mask_mode", "partial"),
        "exclusion_words": set(
            w.strip() for w in (rule.get("exclusion_words", "") or "").split(";") if w.strip()
        ),
    }


def _fallback_rules() -> list[dict]:
    """Use DEFAULT_RULES when DB is not available."""
    return [_build_rule_dict(r) for r in DEFAULT_RULES if r.get("is_active", True)]


def load_rules(force: bool = False) -> list[dict]:
    """Load active rules from DB with in-memory cache (TTL = pii_cache_ttl).

    Falls back to DEFAULT_RULES if DB is not available.
    单条规则 pattern 非法时仅跳过该条并记 warning——旧实现整包 try/except，
    一条坏规则会让全部自定义规则静默回退默认，无任何告警。
    """
    global _rule_cache, _cache_ts
    now = time.time()
    if not force and _rule_cache is not None and (now - _cache_ts) < settings.pii_cache_ttl:
        return _rule_cache

    session = None
    try:
        from app.store.db import get_session, SensitiveRule
        session = get_session()
        rows = session.query(SensitiveRule).filter(SensitiveRule.is_active == True).all()
    except Exception:
        logger.warning("PII rules: DB unavailable, falling back to default rules")
        _rule_cache = _fallback_rules()
        _cache_ts = now
        return _rule_cache
    finally:
        if session is not None:
            session.close()

    compiled = []
    for r in rows:
        if not r.pattern:
            continue
        try:
            pat = re.compile(r.pattern)
        except re.error:
            logger.warning("PII rules: invalid pattern for rule %r skipped: %s",
                           r.rule_name, r.pattern[:80])
            continue
        compiled.append({
            "rule_name": r.rule_name,
            "pattern": pat,
            "validation_fn": r.validation_fn,
            "strategy": r.strategy,
            "mask_mode": r.mask_mode,
            "exclusion_words": set(
                w.strip() for w in (r.exclusion_words or "").split(";") if w.strip()
            ),
        })
    _rule_cache = compiled
    _cache_ts = now
    return _rule_cache


def invalidate_cache():
    """Force reload rules on next scan (call after rule update)."""
    global _rule_cache, _cache_ts
    _rule_cache = None
    _cache_ts = 0


def _has_exclusion(text: str, match_start: int, match_end: int, exclusion_words: set[str]) -> bool:
    """Check if exclusion words appear within a window before/after the match.

    含 CJK 的排除词用子串匹配——`\b` 在汉字之间不存在词边界，
    旧实现对中文排除词（示例/测试/例如等）永不命中，防误报形同虚设。
    """
    if not exclusion_words:
        return False
    window = text[max(0, match_start - 20): min(len(text), match_end + 20)]
    for word in exclusion_words:
        if re.search(r'[一-鿿]', word):
            if word in window:
                return True
        elif re.search(r'\b' + re.escape(word) + r'\b', window, re.IGNORECASE):
            return True
    return False


def scan(text: str) -> list[PiiMatch]:
    """Run all active rules against text: regex → validation → context exclusion.

    Returns list of PiiMatch tuples (sorted by position).

    三层防御(每条规则):
      1. 正则 `rule["pattern"].finditer` 找出候选
      2. 算法校验 `validator(matched)` 验证(身份证校验位/Luhn/手机号段位)
      3. 上下文 `_has_exclusion(...)` 检查前后 20 字符是否含示例关键词
    """
    if not text:
        return []

    rules = load_rules()
    findings: list[PiiMatch] = []

    for rule in rules:
        if not rule["pattern"]:
            continue
        # 三层防御:re.match → validation_fn → exclusion_words 过滤示例文本
        for m in rule["pattern"].finditer(text):
            matched = m.group()
            # Algorithm validation
            vfn_name = rule["validation_fn"]
            validator = VALIDATORS.get(vfn_name)
            if validator and not validator(matched):
                continue
            # Context exclusion
            if _has_exclusion(text, m.start(), m.end(), rule["exclusion_words"]):
                continue
            findings.append(PiiMatch(
                rule_name=rule["rule_name"],
                strategy=rule["strategy"],
                mask_mode=rule["mask_mode"],
                start=m.start(),
                end=m.end(),
                matched_text=matched,
            ))

    findings.sort(key=lambda x: x.start)
    return findings


def _partial_mask(value: str) -> str:
    """Partially mask sensitive data: keep first 3 and last 4 chars."""
    if len(value) <= 1:
        return value
    if len(value) <= 3:
        return value[0] + "*" * (len(value) - 1)
    if len(value) <= 7:
        return value[0] + "*" * (len(value) - 2) + value[-1]
    return value[:3] + "*" * (len(value) - 7) + value[-4:]


def mask_text(text: str, findings: list[PiiMatch] | None = None) -> str:
    """Scan and mask text in-place. Replaces sensitive content per strategy.

    - mask (partial): keep first 3 / last 4 chars
    - mask (full): replace entirely with [已脱敏]
    - audit/reject: register as alert (caller must persist), text unchanged

    If `findings` is provided, reuse it; otherwise run scan() internally.

    Returns masked text (may be unchanged if no mask-able findings).
    """
    if findings is None:
        findings = scan(text)
    if not findings:
        return text

    result = list(text)
    offset = 0
    masked_spans: list[tuple[int, int]] = []   # 已掩码的原始区间
    for finding in findings:
        if finding.strategy not in ("mask",):
            continue
        # 与已处理区间重叠的 finding 整体跳过：
        # 多规则命中同一段（如 18 位号同时过 id_card 与 bank_card 的 Luhn）时，
        # 重复替换会让 offset 累加错乱、输出破损
        if any(not (finding.end <= s or finding.start >= e) for s, e in masked_spans):
            continue
        start = finding.start + offset
        end = finding.end + offset
        if finding.mask_mode == "partial":
            replacement = _partial_mask(finding.matched_text)
        else:
            replacement = "[已脱敏]"
        delta = len(replacement) - (end - start)
        result[start:end] = replacement
        offset += delta
        masked_spans.append((finding.start, finding.end))

    return "".join(result)


def scan_and_reject(text: str) -> list[PiiMatch]:
    """Scan text and return all reject-level findings.

    Caller should:
    1. Check if this returns non-empty
    2. If so, reject the document (stop processing, create PiiAlert records)
    3. If empty, proceed with mask_text() for safe text
    """
    findings = scan(text)
    return [f for f in findings if f.strategy == "reject"]
