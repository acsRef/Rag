"""锁定 vision 走 settings.vision_model，IMAGE_DESCRIBE_PROMPT 保持[类型]契约。

背景：DeepSeek-OCR 烟测输出垃圾字符且不守[类型]前缀，已回退到 Qwen-VL 系列。
原 Qwen/Qwen2.5-VL-7B-Instruct 已在硅基流动下架（400 Model does not exist），
改用可用的 Qwen/Qwen3-VL-8B-Instruct。本测试防止 vision_model 被误改回不可用/不守契约的模型。
"""
from app.config import settings
from app.llm.vision import IMAGE_DESCRIBE_PROMPT


async def test_vision_uses_configured_model(monkeypatch):
    from app.llm.chat import minimax_client
    from app.llm.vision import image_describer

    seen = {}

    async def fake_chat(messages, **kw):
        seen["kw"] = kw
        return "[表格] 提取到的文字"

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    monkeypatch.setattr(image_describer, "_should_skip", lambda *a, **k: False)
    out = await image_describer.describe(b"x" * 6000, "a.png")
    assert seen["kw"].get("model") == settings.vision_model
    assert "Qwen/Qwen3-VL-8B-Instruct" in settings.vision_model


def test_prompt_has_type_contract():
    assert "[类型]" in IMAGE_DESCRIBE_PROMPT
    assert "流程图" in IMAGE_DESCRIBE_PROMPT
    assert "原文" in IMAGE_DESCRIBE_PROMPT
