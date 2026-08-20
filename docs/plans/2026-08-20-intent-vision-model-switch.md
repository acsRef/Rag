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

- **已实测（2026-08-20，真实 API）**：
  1. **DeepSeek-R1 意图**——强化 prompt 前 3/3 不吐 JSON（吐推理轨迹/长段回答，`robust_json_parse` 失败→全库回退）。强化 prompt（禁思考 + 整个回复即 JSON）后**稳定吐 JSON**：三一营收→`docs-a 0.9`、2024vs2025→`docs-a 0.85`，延迟 25-30s→7-17s。但"无关即空数组"的语义判断在弱语义测试环境仍误路由，需在真实 KB 名称下用全量评测复核。
  2. **DeepSeek-OCR**——对白底黑字图输出一串 `}` 垃圾字符、不守 `[类型]` 前缀、丢关键数字。**已回退**。且回退目标 `Qwen/Qwen2.5-VL-7B-Instruct` 已在硅基流动**下架**（400 Model does not exist），改用 `Qwen/Qwen3-VL-8B-Instruct`：延迟 8.6s、`[文档]` 前缀 ✓、数字 `732.22` 原文保留 ✓。
- 已提交：`9bdfdac`（模型切换）、后续 `fix(vision)` 回退 + `fix(intent)` prompt 强化。
- 加了 2 个锁定测试：`tests/unit/test_vision_prompt.py`、`tests/unit/test_intent_tokens.py`。
- **全量评测回归（65 题）**：进行中，见 `docs/plans/2026-08-20-intent-vision-model-validate.md`。

## 风险点定论

1. **R1 推理模型延迟**：已证实是真实风险——加固前 R1 把路由当问答、输出大段推理吃掉 `max_tokens` 并丢 JSON。**已通过强化 prompt 解决**（禁思考 + 整个回复即 JSON）。注意：这也验证了"R1 不适合当第一层意图分类器"的预期，只是本项目目前能救回。
2. **DeepSeek-OCR 非通用多模态**：已证实是硬伤（输出垃圾）——**已放弃 OCR，回退 Qwen-VL 系列**。

## 下一步

1. 全量评测（65 题）出结果后，确认意图路由改动未拉低整体 A/C/H 类分数；
2. 尤其关注"无关问题误路由"是否在真实 KB 名称下降级（若误路由明显，考虑给 intent 加置信度阈值或回退 chat_model）；
3. 将评测结果回填本计划。

## 目标

- 意图路由更稳（KB 命中不误判）
- 图片内容可被检索（OCR 纯文本抽取），评分不降