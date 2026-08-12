# 前端断开后台保活（sse-disconnect-continue）设计

> **状态**：设计评审通过，待细化实施计划。分支建议：`feat/sse-disconnect-continue`
> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪。原则：**每个修复先落测试**（新纯逻辑 → `tests/unit` 离线单测；DB/并发 → `tests/integration` ragent_test 库），再改实现；全部完成后全量回归 + 运行时实测。

**Goal:** 客户端断开（点停止 / 关页 / 断网）时，后端**不再停链**——在后台把当前回答生成完整并落库 `status=completed`，用户回来看到完整答案；前端在后台生成期间禁止同会话发送新消息。

**Architecture:** 方案 A（队列 + 后台生产者）。改动集中在 `app/api/chat.py` 一个文件 + `ChatView.vue` + `memory.py` 一行过滤；**pipeline 本体零改动**（现有 `except GeneratorExit` 保留为服务关停兜底）。不引入新依赖、不改表结构。

**Tech Stack:** FastAPI asyncio、pytest（unit / integration）、Vue 3 + TypeScript。

---

## Context

2026-08-12 审查「前端终止对话时后端链路是否保活」发现：

1. **现状**：`pipeline.py:406-424` 已有 `except GeneratorExit`——客户端断开时把已流出的部分答案落 `status="interrupted"` 并保存 diagnostics。**链路停在这一步**：LLM 流被取消，完整答案永远不会生成；用户回来看到半截回答。
2. **上下文污染**：`memory.py:128` `get_history` 只排除 `streaming`，不排除 `interrupted`——半截回答会进入后续轮次的 LLM 上下文。
3. **前端**：`ChatView.vue` `abortStream()` 断开 fetch，UI 本地复位，无任何「后台继续」感知；`send()` 守卫只查 `streaming`。

**决策（与用户对齐）**：
- 断开后**后台跑完**，完整答案落 `completed`（接受用户主动停止时也烧 LLM token 的代价）。
- 前端 abort 后提示「回答仍在后台生成」，**不轮询常驻**，仅在有后台任务时做有边界轮询。
- 后台生成期间**同会话禁发新消息**（避免消息乱序：Q1 abort → 后台跑 A1 → 用户发 Q2 → A1 后落库变 Q1,Q2,A2,A1）；新对话不受限。
- 改动最小、高内聚低耦合、遵循现有代码规范。

---

## Architecture

### 核心机制：chat.py 队列 + 生产者/消费者

```
请求进来 stream_chat
  → 幂等 get_or_create_conversation 拿 conv_id（pipeline:128 那句保留，两次调用幂等）
  → 检查 _IN_FLIGHT[conv_id] 活跃 → 409（安全网，正常前端已挡）
  → 启动生产者 task：跑 pipeline 生成器，事件写 asyncio.Queue（有界 256）
  → StreamingResponse 消费者：从队列读事件转发客户端
```

**断开时**（客户端 abort / 关页 / 断网）：
- 消费者 `finally`：清空队列 + 置 `connected = False`
- 生产者下一轮循环检查 `connected == False` → **跳过入队**（丢弃模式），pipeline 继续：LLM 生成 → 落库 `completed` → 写 diagnostics → 任务自然结束
- 生产者任务引用存于 `_IN_FLIGHT`，不被 GC、不随响应取消

**关键约束**：
1. 生产者**不被**消费者取消——消费者只停读队列，生产者靠标志位自行切换丢弃模式。
2. 背压沿现有语义：队列满时生产者 `await queue.put` 阻塞（慢客户端限速）；断开后队列被清空，阻塞的 put 立即返回，下轮检查标志跳过。
3. `except GeneratorExit` 保留为兜底：服务关停/生产者被外部取消时仍落 `interrupted`。
4. 生产者异常：done 回调记日志；`_IN_FLIGHT` 移除时确认「还是自己」再删，避免误删新任务。

### 注册表 + 状态端点 + 服务关停

```python
_IN_FLIGHT: dict[str, asyncio.Task] = {}   # conversation_id → 生产者 task（chat.py 模块级）
```

- **状态端点** `GET /api/v1/chat/conversations/{conversation_id}/generating` → `{"generating": bool}`；校验调用者是该会话主人（复用 `get_messages` 的归属检查）。
- **409 安全网**：`stream_chat` 入口发现同会话活跃 → HTTP 409「该对话仍在生成回答」。
- **服务关停**：lifespan 里 `cancel()` 所有 `_IN_FLIGHT` 任务 → 走 GeneratorExit 兜底 → 半截答案落 `interrupted`，不丢状态。

### 前端：ChatView.vue

1. `abortStream()` 后置 `backgroundGen`（记录「哪个会话在后台生成」）。
2. `send()` 守卫加：当前会话后台生成中 → 禁发 + 输入框禁用 + 提示「上一轮回答仍在后台生成…」。
3. **有边界轮询**：仅当存在后台会话时每 2s 查 `/generating`；返回 `false` → 停轮询、`loadMessages` 重载完整答案、恢复可发送。
4. 切换会话：新对话不受限；切回仍在后台生成的会话 → 同样禁发 + 提示。
5. 会话列表 `updated_at` 因落库自动更新，无需额外处理。

### 小修：memory.py get_history 排除 interrupted

`memory.py:128` 改为 `if not m.content or m.status in ("streaming", "interrupted")`。半截话不进 LLM 上下文（后台跑完方案下只在服务关停时出现，但排掉更干净）。落 unit。

---

## Error handling

| 场景 | 行为 |
| ----- | ---- |
| 客户端断开 | 消费者退出，生产者切丢弃模式跑完，落 `completed` |
| 服务关停 | lifespan cancel 生产者 → GeneratorExit → 落 `interrupted` + diag 保存 |
| 生产者异常 | done 回调记日志；注册表 finally 移除（确认是自己） |
| 同会话新请求（绕过前端） | 409 |
| 队列满（慢客户端） | 生产者背压 await put，语义不变 |

## Testing

- **integration**（ragent_test + fake LLM）：慢速流式 fake → 消费者取前 N 个事件即断开 → 断言最终 DB 有**完整**助手消息 `status=completed`；同会话活跃时新请求 409；`/generating` 生成中 true → 完成后 false。
- **unit**：`_IN_FLIGHT`「移除时确认还是自己」逻辑；`get_history` 排除 `interrupted`。
- **收尾**：全量 `pytest` + `npm run build`（vue-tsc）+ `python -m app.main` import 检查。
- **运行时实测**：起后端+前端 → 发消息 → 中途点停止 → 看后端日志确认后台继续跑 → 回会话确认完整答案 + 重新可用。

## Non-goals

- 实时续传（用户回来看到生成过程）——不做。
- 跨 worker 后台任务（注册表进程内，多 uvicorn worker 下仅存活在发起请求的 worker）——既有架构边界，文档注明，不引入 Redis。
- 后台任务配额/速率限制——单用户/小规模应用不做。