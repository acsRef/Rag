"""LLM 调用收敛测试：单次客户端、退避封顶、JSON 契约、限流器钳制、rewrite 守卫、vision。"""

import asyncio

from app.llm.base import jittered_backoff, robust_json_parse


def test_jittered_backoff_capped_at_60s():
    assert jittered_backoff(10) < 60.5  # 2**10 = 1024，未封顶会爆炸
    assert jittered_backoff(0) < 1.5


def test_robust_json_array_returns_none():
    assert robust_json_parse("[1, 2, 3]") is None
    assert robust_json_parse("```json\n[1]\n```") is None


async def test_rate_limiter_never_goes_negative_under_clock_skew(monkeypatch):
    import app.llm.embedding as embedding_mod
    from app.llm.embedding import RateLimiter

    clock = [100.0]

    def skewed():
        clock[0] -= 1.0  # 每次读取都回拨
        return clock[0]

    monkeypatch.setattr(embedding_mod.time, "monotonic", skewed)
    limiter = RateLimiter(rps=5)
    limiter.tokens = 3.0
    for _ in range(3):
        await limiter.acquire()
    assert limiter.tokens >= 0


async def test_rewrite_falls_back_when_sub_questions_empty(monkeypatch):
    from app.core.rewrite import query_rewrite_service
    from app.llm.chat import minimax_client

    async def fake_chat(messages, **kw):
        return '{"rewritten_query": "改写后的查询", "sub_questions": []}'

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    result = await query_rewrite_service.rewrite("原始问题", [], "")
    assert result.sub_questions == ["改写后的查询"]


def test_system_only_template_aligns_with_system_prompt():
    from app.core.prompt import SYSTEM_ANSWER_TEMPLATE

    assert "基于自身知识" not in SYSTEM_ANSWER_TEMPLATE  # 与 system 规则打架的措辞
    assert "不要编造" in SYSTEM_ANSWER_TEMPLATE


def test_should_skip_small_images():
    from app.llm.vision import image_describer

    assert image_describer._should_skip(b"x" * 100) is True
    assert image_describer._should_skip(b"x" * (6 * 1024)) is False


async def test_describe_skips_small_image_without_llm(monkeypatch):
    from app.llm.vision import image_describer

    async def must_not_be_called(*a, **kw):
        raise AssertionError("小图不应调用 LLM")

    monkeypatch.setattr("app.llm.vision.minimax_client.chat", must_not_be_called)
    out = await image_describer.describe(b"x" * 100, "tiny.png")
    assert "跳过" in out


def test_describe_sync_uses_main_loop(monkeypatch):
    import threading

    import app.llm.base as base
    from app.llm.vision import image_describer

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    try:
        base.set_main_loop(loop)
        seen = {}

        async def fake_describe(image_bytes, filename="image.png"):
            seen["loop"] = asyncio.get_running_loop()
            return "desc"

        monkeypatch.setattr(image_describer, "describe", fake_describe)
        out = image_describer.describe_sync(b"y" * 100)
        assert out == "desc"
        assert seen["loop"] is loop  # 跑在主循环上，不再 asyncio.run 新循环
    finally:
        loop.call_soon_threadsafe(loop.stop)
        base.set_main_loop(None)
