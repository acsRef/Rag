"""Image description via MiniMax M3 Vision API — async.

Classifies images into 8 categories and produces concise <100 char descriptions.
Supports concurrent batch processing with MD5 caching and small-image filtering.
"""

import asyncio
import base64
import hashlib
import logging
import os

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

    def __init__(self, max_workers=5, size_threshold=32, file_size_threshold=5 * 1024):
        self.max_workers = max_workers
        self.size_threshold = size_threshold
        self.file_size_threshold = file_size_threshold
        self._semaphore = asyncio.Semaphore(max_workers)
        self._cache: dict[str, str] = {}

    def _image_key(self, content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    def _should_skip(self, image_bytes: bytes, pil_size: tuple[int, int] | None = None) -> bool:
        if len(image_bytes) < self.file_size_threshold:
            return True
        if pil_size and (pil_size[0] < self.size_threshold or pil_size[1] < self.size_threshold):
            return True
        return False

    async def describe(self, image_bytes: bytes, filename: str = "image.png") -> str:
        """Describe a single image via vision API, with cache."""
        key = self._image_key(image_bytes)
        if key in self._cache:
            return self._cache[key]

        suffix = os.path.splitext(filename)[1].lower()
        mime = MIME_MAP.get(suffix, "image/png")
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"

        try:
            resp = await minimax_client.chat([
                {"role": "system", "content": "你是一个图片分析助手，擅长识别图片类型并提取关键信息。"},
                {"role": "user", "content": [
                    {"type": "text", "text": IMAGE_DESCRIBE_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ])
        except Exception as e:
            resp = f"[未知] 图片描述失败：{str(e)}"

        self._cache[key] = resp
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
        return asyncio.run(self.describe(image_bytes, filename))

    def describe_batch_sync(self, images: list[tuple[bytes, str]]) -> list[str]:
        """Sync wrapper for batch description in thread-pool."""
        return asyncio.run(self.describe_batch(images))


image_describer = ImageDescriber()
