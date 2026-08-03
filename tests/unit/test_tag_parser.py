"""TagStreamParser 单测：标签识别、跨 token 边界、展示/持久化一致。"""
from app.core.tag_parser import (
    TagStreamParser, THINK_OPEN, THINK_CLOSE, ANSWER_OPEN, ANSWER_CLOSE,
)


def _run(tokens):
    p = TagStreamParser()
    events = []
    for t in tokens:
        events.extend(p.feed(t))
    events.extend(p.flush())
    return p, events


def _text_of(events, kind):
    return "".join(e["text"] for e in events if e["kind"] == kind)


def test_plain_text_is_answer():
    p, events = _run(["你好，", "世界"])
    assert _text_of(events, "answer") == "你好，世界"
    assert _text_of(events, "thinking") == ""
    assert p.answer_text == "你好，世界"


def test_think_block_routed_to_thinking():
    p, events = _run([THINK_OPEN, "推理过程", THINK_CLOSE, "最终答案"])
    assert _text_of(events, "thinking") == "推理过程"
    assert _text_of(events, "answer") == "最终答案"
    assert p.thinking_text == "推理过程"
    assert p.answer_text == "最终答案"


def test_answer_tags_are_stripped():
    p, events = _run([THINK_OPEN, "t", THINK_CLOSE, ANSWER_OPEN, "答案正文", ANSWER_CLOSE])
    answer = _text_of(events, "answer")
    assert "答案正文" in answer
    assert "answer>" not in answer


def test_answer_tag_without_think():
    p, events = _run([ANSWER_OPEN, "直接答案", ANSWER_CLOSE])
    assert _text_of(events, "answer") == "直接答案"


def test_partial_tag_across_tokens_not_leaked():
    # THINK_OPEN 的前 5 字符（含未闭合标签片段）不得作为文本发出
    p = TagStreamParser()
    events = p.feed(THINK_OPEN[:5])
    assert _text_of(events, "answer") == ""     # 旧实现会泄漏片段
    events = p.feed(THINK_OPEN[5:] + "思考" + THINK_CLOSE)
    assert _text_of(events, "thinking") == "思考"


def test_partial_close_tag_across_tokens_not_leaked():
    p = TagStreamParser()
    assert p.feed(THINK_OPEN) == []               # 先进入 think 状态
    e1 = p.feed("内容" + THINK_CLOSE[:6])
    assert _text_of(e1, "thinking") == "内容"     # 标记前的文本正常发出
    e2 = p.feed(THINK_CLOSE[6:] + "答案")
    assert _text_of(e2, "thinking") == ""         # 标签片段不泄漏进思考
    e3 = p.flush()
    ev = e1 + e2 + e3                             # 答案可能在 e2 已发出，按总量断言
    assert _text_of(ev, "answer") == "答案"
    assert p.thinking_text == "内容"
    assert p.answer_text == "答案"


def test_display_matches_persisted():
    content = "一段很长的纯文本回答，没有任何标签，" * 5
    p, events = _run([content])
    assert _text_of(events, "answer") == content
    assert _text_of(events, "answer") == p.answer_text    # 展示与持久化同源


def test_flush_emits_pending_normal_buffer():
    p = TagStreamParser()
    events = p.feed("尾部短")
    events += p.flush()
    assert _text_of(events, "answer") == "尾部短"


def test_flush_in_think_keeps_thinking():
    p = TagStreamParser()
    events = p.feed(THINK_OPEN + "未闭合的思考")
    events += p.flush()
    assert _text_of(events, "thinking") == "未闭合的思考"
    assert _text_of(events, "answer") == ""


def test_partial_prefix_before_mark_in_same_buffer():
    """回归：文本尾部为标签前缀、同一缓冲内紧跟完整标记——
    旧实现会在此零进展死循环（曾卡死事件循环 40 分钟）。"""
    p = TagStreamParser()
    events = p.feed("abc" + " " + chr(10) + "<" + "think>推理")
    events += p.flush()
    # THINK_OPEN 以 空格+换行 开头：abc 后的空格属标记本身，被整体消费
    assert _text_of(events, "answer") == "abc"
    assert _text_of(events, "thinking") == "推理"


def test_partial_prefix_then_mark_across_tokens():
    """开标签跨 token 拆分（契约：标签以 空格+换行 开头，裸 <think> 是普通文本）。"""
    p = TagStreamParser()
    e1 = p.feed("abc" + THINK_OPEN[:3])       # 尾部保留标记前缀
    e2 = p.feed(THINK_OPEN[3:] + "推理" + THINK_CLOSE + "答")
    e3 = p.flush()
    ev = e1 + e2 + e3
    assert _text_of(ev, "answer") == "abc答"
    assert _text_of(ev, "thinking") == "推理"


def test_empty_token():
    p = TagStreamParser()
    assert p.feed("") == []


def test_lone_lt_at_end_is_emitted_on_flush():
    p = TagStreamParser()
    events = p.feed("价格 <")
    events += p.flush()
    assert _text_of(events, "answer") == "价格 <"


def test_text_before_think_is_answer():
    p, events = _run(["前言文本", THINK_OPEN, "思考", THINK_CLOSE, "答案"])
    assert "前言文本" in _text_of(events, "answer")
    assert _text_of(events, "thinking") == "思考"
    assert _text_of(events, "answer").endswith("答案")
