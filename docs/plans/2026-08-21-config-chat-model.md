# Phase 2 Config Update — chat model 默认切换 V3 → Qwen3-8B (2026-08-21)

## 1. Scope

将 `app/config.py` 默认模型切换到 Qwen3-8B（用户 2026-08-21 成本决策）：

| 字段 | 旧值 | 新值 |
| --- | --- | --- |
| `chat_model` | `deepseek-ai/DeepSeek-V3` | `Qwen/Qwen3-8B` |
| `intent_model` | `deepseek-ai/DeepSeek-V3` | `Qwen/Qwen3-8B` |

**触发原因**：DeepSeek-V3 API 费用超出预算；调研后改用 Qwen3-8B（中文 RAG 任务上效果相近；路由分类任务较轻，Qwen3-8B 足够；统一模型栈降低切换成本）。

## 2. 影响范围

- `app/config.py:11,12` 两个默认模型字段
- `CLAUDE.md` Architecture snapshot "LLM providers" 行（同时改 chat 与 intent）
- `CLAUDE.md` "Required environment variables" 表（"Chat (Qwen3-8B)" + "Intent (Qwen3-8B)"）
- `README.md` 技术栈表 + 关键配置节

## 3. Implementation

- [ ] `app/config.py`：`chat_model` + `intent_model` 默认值切换
- [ ] `CLAUDE.md` "LLM providers" 同步
- [ ] `CLAUDE.md` "Required environment variables" 同步
- [ ] `README.md` 技术栈表同步
- [ ] `README.md` 关键配置节同步
- [ ] `pytest tests/unit -q` 守住基线
- [ ] `python -c "import app.main"` 通过
- [ ] `ruff check` 全绿

## 4. Out of Scope

- 不改 `rewrite_model`（仍为 R1，复杂查询拆解专用）
- 不改 `vision_model` / `embedding_model` / `rerank_model`
- B1 Stage 2 plan 已通过 env override 适配此变更（不需再改 plan）
- B2（query_type DELETE）独立

## 5. Validation

- 测试基线 `459 passed / 6 failed / 13 skipped`（unit 子集 `400 passed / 2 failed`）守口
- 6 failed 预存失败集合不变
- ruff 全绿

## 6. Risk

- 模型名 `Qwen/Qwen3-8B` 在 SiliconFlow 上若不可用需回滚
- 行为变化（影响所有用默认 chat_model 的代码路径）—— 但单元测试不依赖真实 chat，测试守口即认为行为安全
- 若 live eval 后续需重做 baseline，73.3% 历史 baseline 失效
