"""Image description via MiniMax M3 Vision API.

Classifies images into 8 categories (flowchart, architecture, chart, table, code,
UI, document scan, illustration) and produces concise <100 char descriptions.
Supports concurrent batch processing with MD5 caching and small-image filtering.
"""

import base64
import hashlib
import os
import concurrent.futures
from app.llm.chat import minimax_client


MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}

IMAGE_DESCRIBE_PROMPT = """你是一个专业的图片分析助手。请分析这张图片，按以下要求处理。

## 第一步：判断图片类型
从以下类型中选择最匹配的一个：
- 流程图 / 思维导图：包含流程节点、分支、箭头、判断框
- 架构图 / 拓扑图：展示系统组件、模块分层、数据流向
- 图表（柱状图/折线图/饼图/雷达图等）：有坐标轴、数据点、图例
- 表格 / 清单：行列结构，内容对齐
- 代码截图：编程语言代码片段，有语法高亮
- UI 界面 / 网页截图：应用程序界面、弹窗、网页布局
- 文档扫描件 / 截图：纯文字截图，无特殊结构
- 普通插图：照片、示意图、icon 等

## 第二步：提取关键信息

### 流程图/思维导图
提取核心节点和流转关系，重点关注：
- 起始节点和终止节点
- 判断/分支条件
- 主要流程步骤
- 输出结果

示例输出：「[流程图] 用户登录流程：输入账号密码 → 校验身份 → 校验通过则进入首页，失败则返回错误提示」

### 架构图/拓扑图
提取组件名称和层级关系，重点关注：
- 系统分层结构
- 各模块功能
- 数据交互关系

示例输出：「[架构图] 三层架构：展示层(Web/App) → 业务层(用户服务/订单服务) → 数据层(MySQL/Redis)」

### 图表
提取数据趋势和关键数值，重点关注：
- 横纵坐标含义
- 最大值/最小值/拐点
- 数据对比关系

示例输出：「[折线图] 2024年月活用户趋势：1月最低(5万)，逐月上升，12月达峰值(15万)，Q4增速明显」

### 表格/清单
提取表头结构和核心数据行，重点关注：
- 列名和行名
- 关键数值
- 异常值或特殊标记

示例输出：「[表格] 项目进度表：设计(100%)、开发(75%)、测试(50%)，整体进度70%」

### 代码截图
提取函数结构和核心逻辑，重点关注：
- 编程语言
- 主要函数/类名
- 关键算法或业务逻辑

示例输出：「[代码] Python: train_model(data, labels) 函数，使用RandomForestClassifier，n_estimators=100」

### UI 界面/网页截图
提取页面布局和核心功能，重点关注：
- 页面类型（登录页/列表页/详情页等）
- 主要功能按钮和入口
- 页面结构布局

示例输出：「[UI] 登录页面：顶部Logo，中间账号/密码输入框，底部登录按钮，支持微信扫码登录」

### 文档扫描件/截图
直接提取文字内容，去除无关背景干扰。

示例输出：「[文档] 会议纪要：2024年Q1营收增长15%，达成预期目标，Q2计划拓展海外市场」

### 普通插图
一句话概括图中主要内容。

示例输出：「[插图] 两只熊猫在竹林中吃竹子」

## 要求
- 控制在 100 字以内，只输出关键信息
- 不要开场白和结束语
- 格式：以「[类型]」开头
- 如果图片模糊或无法识别，输出「[未知] 图片无法识别」"""


class ImageDescriber:
    """Describes images via MiniMax M3 Vision API with classification prompts.

    Features:
    - 8-category image classification with tailored extraction prompts
    - MD5 content-addressable cache to avoid redundant API calls
    - Small-image filtering (file < 5KB or dimension < 32×32)
    - Concurrent batch processing via ThreadPoolExecutor
    - Single-image failure never blocks the batch
    """

    def __init__(self, max_workers=5, size_threshold=32, file_size_threshold=5 * 1024):
        self.max_workers = max_workers
        self.size_threshold = size_threshold  # min width/height in pixels
        self.file_size_threshold = file_size_threshold  # min file size in bytes
        self._cache: dict[str, str] = {}

    def _image_key(self, content: bytes) -> str:
        """MD5 hash for cache key."""
        return hashlib.md5(content).hexdigest()

    def _should_skip(self, image_bytes: bytes, pil_size: tuple[int, int] | None = None) -> bool:
        """Skip images below file size or pixel dimension thresholds."""
        if len(image_bytes) < self.file_size_threshold:
            return True
        if pil_size and (pil_size[0] < self.size_threshold or pil_size[1] < self.size_threshold):
            return True
        return False

    def describe(self, image_bytes: bytes, filename: str = "image.png") -> str:
        """Describe a single image via vision API, with cache."""
        key = self._image_key(image_bytes)
        if key in self._cache:
            return self._cache[key]

        suffix = os.path.splitext(filename)[1].lower()
        mime = MIME_MAP.get(suffix, "image/png")
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"

        try:
            resp = minimax_client.chat([
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

    def describe_batch(self, images: list[tuple[bytes, str]]) -> list[str]:
        """Describe multiple images concurrently; a single failure returns fallback text."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [ex.submit(self.describe, content, name) for content, name in images]
            results = []
            for f in futures:
                try:
                    results.append(f.result(timeout=30))
                except Exception as e:
                    results.append(f"[未知] 处理超时或失败")
            return results


image_describer = ImageDescriber()
