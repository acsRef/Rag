# 意图路由 + 视觉模型切换（2026-08-20）

## 本次完成

将意图识别与图片识别的模型切换到 DeepSeek（SiliconFlow 轨迹平台）：

| 环节 | 旧模型 | 新模型 |
| ---- | ------ | ------ |
| 意图路由 | DeepSeek-V3（默认 chat_model） | `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` |
| 视觉/图片识别 | `Qwen/Qwen2.5-VL-7B-Instruct` | `deepseek-ai/DeepSeek-OCR` |

改动点：
- `app/config.py` — 新增 `intent_model` 配置项；`vision_model` 改为 DeepSeek-OCR
- `app/core/intent.py` — `classify()` 调用带上 `model=settings.intent_model`
- `app/llm/vision.py` — 系统指令并入 user 消息（DeepSeek-OCR 对 system 角色支持不一，OCR 模型最稳妥格式）
- 同步更新 CLAUDE.md / README.md / chat.py docstring 中的模型引用

验证：导入链 OK（三个模型读取正确）；`tests/unit/test_intent_guard.py` + `test_llm_gateway.py` 17 个测试全过；intent 调用确认传入 `model=deepseek-ai/DeepSeek-R1-0528-Qwen3-8B`。

## 当前状态

- 未经真实 API 实测：`.env` 需要有效 SILICONFLOW_API_KEY，且需起库+起服务后跑一次意图路由 + 图片上传验证。
- 风险点（换用前需实测定夺）：
  1. **R1 推理模型延迟**：意图路由在每次查询的关键路径上（每个 sub-question 一次），推理 token 会拖慢首 token；`chat()` 的 `max_tokens` 默认 4096，推理占 budget 若截断 JSON 会走兜底（全库回退）。
  2. **DeepSeek-OCR 非通用多模态**：OCR 强于文字提取（文档扫描/截图/表格），弱于"流程图/架构图分类"这类通用图文理解；`IMAGE_DESCRIBE_PROMPT` 的 `[类型]` 前缀约定可能不被遵守。

## 下一步

1. 实测（优先级高）：起服务，跑一次意图路由（多 KB 场景）+ 上传一张含图文档，看 OCR 输出格式；
2. 实测后按需调优：
   - 若 R1 意图延迟高/丢 JSON → 给 intent 调大 `max_tokens` 或退回 chat_model 路由；
   - 若 OCR 输出不合 `[类型]` 约定 → 改写 `IMAGE_DESCRIBE_PROMPT` 为 OCR 友好的"提取图中文字"指令；
3. 复跑评测（eval_sany.py 65 题）确认意图路由改动未拉低整体 A/C/H 类分数；
4. 将结果回填本计划「当前状态」。

## 目标

- 意图路由更稳（KB 命中不误判）
- 图片内容可被检索（OCR 纯文本抽取），评分不降