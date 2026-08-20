"""一次性烟测：DeepSeek-R1 意图 + DeepSeek-OCR 视觉（真实 API，需 .env key）。

用法:
    D:/miniConda/envs/rag/python.exe tools/smoke_models.py

不出图测试: 用 PIL 现画一张含中文文字的 PNG，无需上传正文。
"""
import asyncio
import io
import time

from app.config import settings
from app.llm.chat import minimax_client

INTENT_PROBE_QUESTIONS = [
    "三一重工2023年营业收入是多少？",
    "科创板开户需要什么条件？",          # 与知识库无关，应返回空 matches
    "2024年与2025年研发投入对比如何？",   # 多 KB 场景
]


def _make_text_image(width=600, height=200, lines=("三一重工 2023年 营业收入 732.22亿元", "研发费用 同比增长 18%")):
    """用 PIL 现画含中文的图片，避免依赖本地图片文件。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:
        font = None
    y = 20
    for ln in lines:
        d.text((20, y), ln, fill="black", font=font)
        y += 50
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def smoke_intent():
    print("=== 意图路由: DeepSeek-R1 ===")
    for q in INTENT_PROBE_QUESTIONS:
        messages = [{"role": "user", "content": f"把问题路由到知识库: {q}\n可用的知识库: ['docs-a', 'docs-b']"}]
        t0 = time.time()
        try:
            resp = await minimax_client.chat(
                messages, model=settings.intent_model, max_tokens=4096,
            )
        except Exception as e:
            print(f"  [{q[:20]}] 调用失败: {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        head = resp[:180].replace("\n", " ")
        print(f"  [{q[:20]}] 延迟 {dt:.1f}s | 长度 {len(resp)} | 前缀: {head}")
        try:
            import json
            json.loads(resp.split("```json")[-1].split("```")[0].strip())
            print("    -> JSON 完整 OK")
        except Exception:
            print("    -> WARN JSON 可能被截断/不纯 (需加固)")


def smoke_vision():
    print("\n=== 视觉: %s ===" % settings.vision_model)
    from app.llm.vision import image_describer
    img = _make_text_image()
    t0 = time.time()
    out = image_describer.describe_sync(img, "smoke.png")
    dt = time.time() - t0
    print(f"  延迟 {dt:.1f}s | 输出: {out[:300]}")
    print(f"  以[类型]开头? {'OK' if out.strip().startswith('[') else 'WARN 未遵循[类型]约定'}")
    print(f"  含 '732.22'? {'OK' if '732.22' in out else 'WARN 数字未保留'}")


if __name__ == "__main__":
    asyncio.run(smoke_intent())
    smoke_vision()
    print("\n烟测完成。把上面 OK/WARN 结果登记到 plan 的结论小节。")
