"""锁定 app/core/pipeline.py 纯辅助函数行为：分解门控 / SSE 转义 / 文本规整。"""

from app.core.pipeline import _needs_decomposition, _norm, _sse_safe

# ── _needs_decomposition ────────────────────────────────

def test_decomp_comparison_pattern():
    assert _needs_decomposition("JWT 和 Session 有什么区别") is True


def test_decomp_multiple_entities():
    assert _needs_decomposition("对比《文档A》和《文档B》") is True


def test_decomp_pronoun():
    assert _needs_decomposition("它的参数是什么") is True


def test_decomp_simple_query_no_trigger():
    assert _needs_decomposition("什么是 RAG") is False


def test_decomp_no_false_positive_on_common_words():
    assert _needs_decomposition("还有其他方案吗") is False
    assert _needs_decomposition("其实我不确定") is False


def test_decomp_qi_ta_not_anaphoric():
    """设计审查 P2-15：`它` 命中的 `其它` 不是代词，不应触发分解。"""
    assert _needs_decomposition("其它方案可以吗") is False
    assert _needs_decomposition("其他方案可以吗") is False


# ── _sse_safe ───────────────────────────────────────────

def test_sse_safe_escapes_newline_and_strips_cr():
    # _NL 是字面 反斜杠+n（两个字符），\r 被删除
    assert _sse_safe("a\nb\rc") == "a" + chr(92) + "nbc"


# ── _norm ───────────────────────────────────────────────

def test_norm_collapses_excess_blank_lines():
    assert _norm("a\n\n\n\nb") == "a\n\nb"


def test_norm_strips_outer_whitespace():
    assert _norm("  hi  ") == "hi"
