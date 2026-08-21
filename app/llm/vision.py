"""Image description via MiniMax M3 Vision API — async.

Classifies images into 8 categories and produces concise <100 char descriptions.
Supports concurrent batch processing with MD5 caching and small-image filtering.
"""

import asyncio
import base64
import hashlib
import logging
import os
from collections import OrderedDict

from app.llm.chat import minimax_client

logger = logging.getLogger(__name__)

MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}

IMAGE_DESCRIBE_PROMPT = (
    "你是一个图片分析助手。分析图片并按以下要求输出。\n"
    "\n"
    "【CRITICAL】控制在 100 字以内，以「[类型]」开头。\n"
    "【CRITICAL】只输出关键信息，不要开场白、结束语、解释。违反将受罚。\n"
    "【CRITICAL】如果图片模糊或无法识别，输出「[未知] 图片无法识别」。不要强行猜测。\n"
    "\n"
    "## 图片类型与提取方式\n"
    "\n"
    "| 类型 | 提取重点 | 示例输出 |\n"
    "|------|----------|----------|\n"
    "| 流程图 | 节点、分支条件、流转关系 | [流程图] 用户登录流程：输入账号→校验身份→进入首页（成功）或错误提示（失败） |\n"
    "| 架构图 | 组件名称、层级关系、数据流 | [架构图] 三层架构：展示层(Web/App)→业务层(用户服务/订单服务)→数据层(MySQL/Redis) |\n"
    "| 图表 | 坐标含义、极值、趋势 | [折线图] 2024年月活用户趋势：1月最低(5万)，逐月上升，12月达峰值(15万) |\n"
    "| 表格 | 列名、关键数据行、异常值 | [表格] 项目进度：设计(100%)、开发(75%)、测试(50%)，整体70% |\n"
    "| 代码截图 | 语言、函数/类、核心逻辑 | [代码] Python: train_model(data, labels)→RandomForestClassifier, n_estimators=100 |\n"
    "| UI截图 | 页面类型、功能按钮、布局 | [UI] 登录页：顶部Logo，中间账号/密码输入框，底部登录按钮 |\n"
    "| 文档扫描件 | 直接提取文字内容 | [文档] 会议纪要：2024年Q1营收增长15%... |\n"
    "| 普通插图 | 一句话概括 | [插图] 两只熊猫在竹林中吃竹子 |\n"
    "\n"
    "## 精度要求\n"
    "- 版本号、数字、API 名称、协议名称、代码片段中的关键词必须原文保留，不要概括。\n"
    "- 人名、地名、公司名、产品名必须原文保留。\n"
    "- 拿不准的细节可以省略，但不要编造。\n"
    "\n"
    "## 输出前确认\n"
    "□ 是否以「[类型]」开头？\n"
    "□ 是否明显编造了不确定的信息？\n"
    "□ 是否超过了 100 字？\n"
    "□ 关键数字和术语是否保留了原文？"
)


class ImageDescriber:
    """Async image describer via MiniMax M3 Vision API.

    Features:
    - 8-category image classification
    - MD5 content-addressable cache
    - Small-image filtering (file < 5KB or dimension < 32×32)
    - Concurrent batch via asyncio.gather
    """

    def __init__(self, max_workers=5, size_threshold=32, file_size_threshold=5 * 1024, max_cache=1000):
        self.max_workers = max_workers
        self.size_threshold = size_threshold
        self.file_size_threshold = file_size_threshold
        self.max_cache = max_cache
        self._semaphore = asyncio.Semaphore(max_workers)
        self._cache: OrderedDict[str, str] = OrderedDict()

    def _cache_get(self, key: str) -> str | None:
        val = self._cache.get(key)
        if val is not None:
            self._cache.move_to_end(key)
        return val

    def _cache_put(self, key: str, val: str):
        if len(self._cache) >= self.max_cache:
            self._cache.popitem(last=False)
        self._cache[key] = val

    def _image_key(self, content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    def _should_skip(self, image_bytes: bytes, pil_size: tuple[int, int] | None = None) -> bool:
        if len(image_bytes) < self.file_size_threshold:
            return True
        return bool(pil_size) and (
            pil_size[0] < self.size_threshold or pil_size[1] < self.size_threshold
        )

    async def describe(self, image_bytes: bytes, filename: str = "image.png") -> str:
        """Describe a single image via vision API, with cache."""
        # 小图过滤（docstring 早已声明，此处正式接线）：省 token 与配额
        if self._should_skip(image_bytes):
            return "[跳过] 图片过小，未调用视觉模型"
        key = self._image_key(image_bytes)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        suffix = os.path.splitext(filename)[1].lower()
        mime = MIME_MAP.get(suffix, "image/png")
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"

        try:
            from app.config import settings as _settings
            # 固定走多模态模型：文本模型可能是非多模态的 highspeed 变体。
            # Qwen2.5-VL 支持 system 角色（DeepSeek-OCR 曾因不支持才并入 user，已回退）。
            resp = await minimax_client.chat([
                {"role": "system", "content": "你是一个图片分析助手，擅长识别图片类型并提取关键信息。"},
                {"role": "user", "content": [
                    {"type": "text", "text": IMAGE_DESCRIBE_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ], model=_settings.vision_model)
        except Exception as e:
            return f"[未知] 图片描述失败：{str(e)}"

        self._cache_put(key, resp)
        return resp

    async def describe_batch(self, images: list[tuple[bytes, str]]) -> list[str]:
        """Describe multiple images concurrently."""
        async def describe_one(content: bytes, name: str) -> str:
            async with self._semaphore:
                try:
                    return await self.describe(content, name)
                except Exception:
                    return "[未知] 处理超时或失败"

        tasks = [describe_one(content, name) for content, name in images]
        return await asyncio.gather(*tasks)

    def describe_sync(self, image_bytes: bytes, filename: str = "image.png") -> str:
        """Sync wrapper for use in thread-pool (e.g. ingestion pipeline)."""
        return self._run_on_loop(self.describe(image_bytes, filename))

    def describe_batch_sync(self, images: list[tuple[bytes, str]]) -> list[str]:
        """Sync wrapper for batch description in thread-pool."""
        return self._run_on_loop(self.describe_batch(images))

    @staticmethod
    def _run_on_loop(coro):
        """优先把协程派发回主事件循环执行。

        旧实现每次 asyncio.run 新建循环，触发全局 minimax_client 按 loop-id
        反复重建（旧 httpx 连接池从不关闭，socket 泄漏）。主循环不可用时
        回落 asyncio.run 兜底。
        """
        from app.llm.base import get_main_loop
        loop = get_main_loop()
        if loop is not None and loop.is_running():
            try:
                return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=180)
            except Exception:
                logger.warning(
                    "vision: main-loop dispatch failed, falling back to local loop",
                    exc_info=True,
                )
        return asyncio.run(coro)


image_describer = ImageDescriber()
