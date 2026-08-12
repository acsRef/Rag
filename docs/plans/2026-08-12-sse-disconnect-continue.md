# 前端断开后台保活（sse-disconnect-continue）设计

> **状态**：已完成（分支 fix/design-review-fixes，commits `396b055`..`a64d037`）。全量 281 passed + 8 新增测试绿；11 个既有 SSL 环境失败（基线同样失败，无关）；npm run build 通过；运行时实测通过（断开后台跑完落 completed / cancel 后可立即重发 / 同会话 409）。
> **分支建议**：`feat/sse-disconnect-continue`
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
- 断开后**后台跑完**，完整答案落 `completed`（接受用户被动离开时也烧 LLM token 的代价）。
- 前端 abort 后提示「回答仍在后台生成」，**不轮询常驻**，仅在有后台任务时做有边界轮询。
- 后台生成期间**同会话禁发新消息**（避免消息乱序：Q1 abort → 后台跑 A1 → 用户发 Q2 → A1 后落库变 Q1,Q2,A2,A1）；新对话不受限。
- **「停止」按钮 = 真正取消**（区别于离开）：`POST /cancel` 取消生产者，半截答案不保留（取消点异），注册表立即清空 → 可马上发新消息。用户显式放弃时不烧 token。
- 「用户直接关浏览器再打开」= 后台跑完 + 落库，打开会话即见完整答案（**硬需求**，已覆盖）。
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
- **取消端点** `POST /api/v1/chat/conversations/{conversation_id}/cancel` → `{"cancelled": bool}`；停止按钮专用：取消生产者并 `await gather` 等清理完成（注册表移除），返回后用户可立即发新消息。归属鉴权同上。
- **409 安全网**：`stream_chat` 入口发现同会话活跃 → HTTP 409「该对话仍在生成回答」。
- **服务关停**：lifespan 里 `cancel()` 所有 `_IN_FLIGHT` 任务。
- **取消/关停的持久化粒度**：取消点落在 LLM 流内时（常见），CancelledError 直穿生成器（`except GeneratorExit` 不捕 CancelledError），半截助手消息**不落库**——用户消息（LLM 前一节已落）保留，无半截污染；取消点落在两个 yield 之间时走 GeneratorExit 落 `interrupted`。两种情况都不丢用户消息、不损坏状态，故不改 pipeline。

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
| 客户端断开（离开/关页/切走） | 消费者退出，生产者切丢弃模式跑完，落 `completed` |
| 用户点「停止」 | `POST /cancel` 取消生产者并等清理 → 注册表清空 → 立即发新消息；半截不保留 |
| 服务关停 | lifespan cancel 生产者 → 半截落 `interrupted`（yield 间）或丢弃（LLM 流内），用户消息不丢 |
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

---

# Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 原则：每项先落测试（integration 用 ragent_test + fake LLM 层），再改实现，全绿后 commit。

**Goal:** 客户端断开后，后端在后台把当前回答生成完整并落库 `completed`；前端在后台生成期间禁发同会话新消息，完成时自动刷出完整答案。

**Architecture:** 方案 A——`chat.py` 加有界队列 + 后台生产者 task：消费者（StreamingResponse）断开时置 `connected=False`，生产者切丢弃模式继续跑完 pipeline（pipeline 本体零改动，`except GeneratorExit` 保留为服务关停兜底）。进程内 `_IN_FLIGHT` 注册表 + `/generating` 状态端点 + 409 安全网 + lifespan 关停清理。

**Tech Stack:** FastAPI asyncio、pytest（unit / integration ragent_test）、Vue 3 + TypeScript。

## File Structure

| 动作 | 文件 | 职责 |
| ----- | ---- | ----- |
| Modify | `app/api/chat.py` | `_IN_FLIGHT` 注册表、`_release_in_flight`/`_log_producer_error`、`shutdown_in_flight_generations`、`stream_chat` 队列+生产者、`/generating` 状态端点、`/cancel` 取消端点 |
| Modify | `app/main.py` | lifespan `yield` 后关停后台生成任务 |
| Modify | `app/core/memory.py:128` | `get_history` 排除 `interrupted` |
| Create | `tests/unit/test_sse_disconnect.py` | 注册表清理纯逻辑 |
| Create | `tests/integration/test_sse_disconnect.py` | 断开后台跑完 / 409 / generating 翻转 / 关停取消 / interrupted 排除 |
| Modify | `frontend/src/api/chat.ts` | 新增 `generating(conversationId)` |
| Modify | `frontend/src/views/ChatView.vue` | `bgConvs` 追踪 + 禁发 + 有边界轮询 + 提示 |

---

### Task 1: integration 测试先落（断开 → 后台跑完）

**Files:**
- Create: `tests/integration/test_sse_disconnect.py`

- [ ] **Step 1: 写失败测试**（对当前代码 red：`_IN_FLIGHT` 尚不存在会 ImportError；即使绕过，断开后当前代码只落 `interrupted` 半截，断言必败）

```python
"""sse-disconnect-continue：客户端断开后，生产者后台跑完并落库 completed。

覆盖：断开→完整答案入库；同会话 409；/generating 状态翻转；服务关停取消；
get_history 排除 interrupted 半截回答。
"""
import asyncio
import time

import pytest

from app.api.chat import (
    _IN_FLIGHT,
    cancel_generation,
    conversation_generating,
    shutdown_in_flight_generations,
    stream_chat,
)
from app.config import settings
from app.models.schemas import ChatRequest
from app.store.db import get_db_ctx, Message

USER = {"id": "test-user", "permissions": ["chat"], "role_ids": [], "is_admin": False}


async def _slow_chat_stream(*args, **kwargs):
    """慢速确定性流：每 token 间 sleep，让「消费到 token 后断开」可控。"""
    for tok in ["后台", "生成", "完成", "！"]:
        yield tok
        await asyncio.sleep(0.05)


async def _wait_until(predicate, timeout=8.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def test_disconnect_background_completes(
        integration_db, fake_llm_stack, monkeypatch):
    from app.llm.chat import minimax_client
    monkeypatch.setattr(minimax_client, "chat_stream", _slow_chat_stream)
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    req = ChatRequest(query="你好", conversation_id="conv-bg")
    response = await stream_chat(req, current_user=USER)
    body = response.body_iterator

    # 消费到第一个 token 事件后模拟断开（aclose 触发消费者 finally）
    saw_token = False
    for _ in range(50):
        evt = await anext(body)
        if "event: token" in evt:
            saw_token = True
            break
    assert saw_token, "断开前应消费到 token 事件"

    task = _IN_FLIGHT.get("conv-bg")
    assert task is not None, "生产者应已注册"
    await body.aclose()          # ↓ 消费者断开
    assert not task.done(), "断开后生产者不应被取消（后台跑完）"
    await asyncio.wait_for(task, timeout=10)

    with get_db_ctx() as session:
        msgs = session.query(Message).filter(
            Message.conversation_id == "conv-bg").order_by(Message.id.asc()).all()
    assert [m.role for m in msgs] == ["user", "assistant"], "断开后应落库完整一问一答"
    assert msgs[-1].status == "completed", "后台跑完应落 completed"
    assert "完成" in (msgs[-1].content or ""), "应为完整回答而非半截"


async def test_same_conversation_409(integration_db, fake_llm_stack, monkeypatch):
    from fastapi import HTTPException
    from app.llm.chat import minimax_client
    monkeypatch.setattr(minimax_client, "chat_stream", _slow_chat_stream)
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    resp1 = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-409"), current_user=USER)
    body1 = resp1.body_iterator
    task = _IN_FLIGHT.get("conv-409")
    assert task is not None and not task.done()

    with pytest.raises(HTTPException) as ei:
        await stream_chat(
            ChatRequest(query="再问", conversation_id="conv-409"), current_user=USER)
    assert ei.value.status_code == 409

    await body1.aclose()
    await asyncio.wait_for(task, timeout=10)


async def test_generating_endpoint_flips(integration_db, fake_llm_stack, monkeypatch):
    from app.llm.chat import minimax_client
    monkeypatch.setattr(minimax_client, "chat_stream", _slow_chat_stream)
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    resp = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-gen"), current_user=USER)
    body = resp.body_iterator
    task = _IN_FLIGHT.get("conv-gen")

    assert conversation_generating("conv-gen", current_user=USER)["generating"] is True

    await body.aclose()
    await asyncio.wait_for(task, timeout=10)
    assert conversation_generating("conv-gen", current_user=USER)["generating"] is False


async def test_shutdown_cancels_background(integration_db, fake_llm_stack, monkeypatch):
    from app.llm.chat import minimax_client
    monkeypatch.setattr(minimax_client, "chat_stream", _slow_chat_stream)
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    resp = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-shutdown"), current_user=USER)
    task = _IN_FLIGHT.get("conv-shutdown")
    await resp.body_iterator.aclose()
    assert task is not None and not task.done()

    await shutdown_in_flight_generations()
    assert task.done(), "关停后后台任务应被取消并结束"
    assert "conv-shutdown" not in _IN_FLIGHT, "注册表应清理"


async def test_cancel_stops_background(integration_db, fake_llm_stack, monkeypatch):
    from app.llm.chat import minimax_client
    monkeypatch.setattr(minimax_client, "chat_stream", _slow_chat_stream)
    monkeypatch.setattr(settings, "diagnostics_enabled", False)

    resp = await stream_chat(
        ChatRequest(query="你好", conversation_id="conv-cancel"), current_user=USER)
    task = _IN_FLIGHT.get("conv-cancel")
    assert task is not None and not task.done()

    res = await cancel_generation("conv-cancel", current_user=USER)
    assert res["cancelled"] is True
    assert "conv-cancel" not in _IN_FLIGHT, "取消后注册表应清空，可立即发新消息"

    with get_db_ctx() as session:
        msgs = session.query(Message).filter(
            Message.conversation_id == "conv-cancel").order_by(Message.id.asc()).all()
    assert msgs and msgs[0].role == "user", "用户消息在取消后仍应保留"
    # 半截助手消息是否落库取决于取消点（yield 间 vs LLM 流内），不强制断言


async def test_interrupted_message_excluded_from_history(integration_db):
    from app.core.memory import conversation_memory
    conv_id = conversation_memory.get_or_create_conversation("conv-hist", "test-user")
    await conversation_memory.add_message(conv_id, "user", "问题一", user_id="test-user")
    await conversation_memory.add_message(
        conv_id, "assistant", "半截回答", status="interrupted", user_id="test-user")
    await conversation_memory.add_message(
        conv_id, "assistant", "完整回答", status="completed", user_id="test-user")
    history = conversation_memory.get_history(conv_id)
    contents = [m["content"] for m in history if m["role"] == "assistant"]
    assert contents == ["完整回答"], "interrupted 半截回答不应进入 LLM 上下文"
```

- [ ] **Step 2: 运行确认 red**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_sse_disconnect.py -q
```
Expected: 全部 FAIL/ERROR（`_IN_FLIGHT` 未定义 → import error；`get_history` 未排除 interrupted → 断言失败）。

- [ ] **Step 3: 提交 red 测试作为记录**

```bash
git add tests/integration/test_sse_disconnect.py
git commit -m "test(sse-disconnect): failing integration tests for background completion"
```

---

### Task 2: chat.py 队列 + 后台生产者 + 注册表

**Files:**
- Modify: `app/api/chat.py`（顶部 import + `stream_chat` 重构 + 新函数）
- Create: `tests/unit/test_sse_disconnect.py`

- [ ] **Step 1: 写 unit 测试（注册表「确认还是自己」清理逻辑）**

```python
"""sse-disconnect-continue：注册表清理纯逻辑（离线）。

客户端断开后生产者后台跑完；注册表移除时须确认当前任务仍是自己，
避免旧任务收尾误删新请求的任务。
"""
import asyncio

from app.api.chat import _IN_FLIGHT, _release_in_flight


def _reset():
    _IN_FLIGHT.clear()


async def _wait_task(t):
    try:
        await t
    except asyncio.CancelledError:
        pass


async def test_release_removes_same_task():
    _reset()
    t = asyncio.create_task(asyncio.sleep(0))
    _IN_FLIGHT["conv-x"] = t
    _release_in_flight("conv-x", t)
    assert "conv-x" not in _IN_FLIGHT
    t.cancel()
    await _wait_task(t)


async def test_release_keeps_newer_task():
    _reset()
    old = asyncio.create_task(asyncio.sleep(0))
    new = asyncio.create_task(asyncio.sleep(0))
    _IN_FLIGHT["conv-x"] = new
    _release_in_flight("conv-x", old)   # 旧任务收尾误调清理
    assert _IN_FLIGHT.get("conv-x") is new
    old.cancel()
    new.cancel()
    await _wait_task(old)
    await _wait_task(new)
```

- [ ] **Step 2: 运行确认 red**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_sse_disconnect.py -q
```
Expected: FAIL（`_release_in_flight` 未定义）。

- [ ] **Step 3: 实现 chat.py**

顶部 import 改为（`stream_chat` 内的 `from fastapi.responses import StreamingResponse` 保留原位）：

```python
"""Chat API with optional auth."""
import asyncio
import logging

from app.core.pipeline import rag_pipeline
from app.core.diagnostics import DiagContext
from app.config import settings
from app.core.memory import conversation_memory
from app.models.schemas import ChatRequest, ConversationResponse
from app.middleware.auth import get_current_user
from app.store.db import get_db_ctx, Conversation
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])

logger = logging.getLogger(__name__)

# 进程内后台生成任务注册表：conversation_id → 生产者 task。
# 客户端断开后生产者继续跑完 pipeline 并落库 completed（见 sse-disconnect-continue plan）。
# 注意：进程内注册表，多 uvicorn worker 下仅覆盖发起请求的 worker（既有架构边界）。
_IN_FLIGHT: dict[str, asyncio.Task] = {}

_SSE_QUEUE_MAX = 256          # SSE 事件队列上限：慢客户端背压；断开后清空解除
_STREAM_END = object()        # 传输层结束哨兵（区别于任何 SSE 事件字符串）


def _release_in_flight(conv_id: str, task: asyncio.Task) -> None:
    """从注册表移除任务：确认还是自己再删，避免旧任务收尾误删新请求的任务。"""
    if _IN_FLIGHT.get(conv_id) is task:
        _IN_FLIGHT.pop(conv_id, None)


def _log_producer_error(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("后台生成任务异常（断开后链路）", exc_info=task.exception())


async def shutdown_in_flight_generations() -> None:
    """服务关停：取消所有后台生成任务。

    生产者捕获 CancelledError 后主动 aclose 生成器，触发 pipeline 的
    GeneratorExit 兜底——半截答案落 interrupted，不丢状态。
    """
    tasks = list(_IN_FLIGHT.values())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

`_build_diag_ctx` 保持不变。`stream_chat` 整体替换为：

```python
@router.post("/stream")
async def stream_chat(
    req: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    from fastapi.responses import StreamingResponse
    if "chat" not in current_user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    user_id = current_user["id"]
    user_role_ids = current_user["role_ids"]
    can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
    ctx = _build_diag_ctx(req.query)

    # 幂等拿 conv_id（pipeline:128 还会再取一次，幂等）——注册表 key 与 409 检查需要它
    conv_id = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation,
        req.conversation_id, user_id,
    )

    # 同会话后台生成中 → 409（安全网；正常前端已禁发）
    active = _IN_FLIGHT.get(conv_id)
    if active is not None and not active.done():
        raise HTTPException(status_code=409, detail="该对话仍在生成回答，请稍后再试")

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_SSE_QUEUE_MAX)
    connected = {"value": True}

    async def producer():
        gen = rag_pipeline.execute(
            req, user_id=user_id, user_role_ids=user_role_ids,
            can_read_all=can_read_all, ctx=ctx,
        )
        try:
            async for evt in gen:
                if not connected["value"]:
                    continue  # 客户端已断开：丢弃模式，pipeline 继续跑完
                await queue.put(evt)
        except asyncio.CancelledError:
            # 服务关停：主动关闭生成器，触发 GeneratorExit 兜底（落 interrupted）
            await gen.aclose()
            raise
        finally:
            try:
                if connected["value"]:
                    # 正常跑完：通知消费者收尾（1s 上限防关停时满队列阻塞）
                    await asyncio.wait_for(queue.put(_STREAM_END), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass  # 消费者已断开/关停：无需送达结束哨兵
            finally:
                _release_in_flight(conv_id, asyncio.current_task())

    task = asyncio.create_task(producer())
    _IN_FLIGHT[conv_id] = task
    task.add_done_callback(_log_producer_error)

    async def event_stream():
        try:
            while True:
                evt = await queue.get()
                if evt is _STREAM_END:
                    break
                yield evt
        finally:
            # 客户端断开：生产者不取消，切丢弃模式继续跑完
            connected["value"] = False
            # 清空队列，唤醒可能阻塞在 put 的生产者（解除背压）
            while not queue.empty():
                queue.get_nowait()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

新增 `conversation_generating` 端点（放在 `get_messages` 之前）：

```python
@router.get("/conversations/{conversation_id}/generating")
def conversation_generating(conversation_id: str, current_user: dict = Depends(get_current_user)):
    """前端有边界轮询：后台生成任务是否仍在运行（归属鉴权与 get_messages 一致）。"""
    with get_db_ctx() as session:
        conv = session.query(Conversation).filter(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == current_user["id"],
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    task = _IN_FLIGHT.get(conversation_id)
    return {"generating": task is not None and not task.done()}


@router.post("/conversations/{conversation_id}/cancel")
async def cancel_generation(conversation_id: str, current_user: dict = Depends(get_current_user)):
    """「停止」按钮：显式取消生成任务。

    与「离开自动后台跑完」不同——用户主动停止 = 放弃该答案。
    取消后等任务清理完成（注册表移除），返回即可立即发新消息；
    半截助手消息是否落库取决于取消点（yield 间走 GeneratorExit 落 interrupted，
    LLM 流内 CancelledError 直穿不落），用户消息始终保留。
    """
    with get_db_ctx() as session:
        conv = session.query(Conversation).filter(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == current_user["id"],
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    task = _IN_FLIGHT.get(conversation_id)
    if task is None or task.done():
        return {"cancelled": False}
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return {"cancelled": True}
```

- [ ] **Step 4: 运行确认 green**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_sse_disconnect.py tests/integration/test_sse_disconnect.py -q
```
Expected: 全部 PASS（integration 需 PG 可达；PG 不可达时 integration 自动 skip，unit 必绿）。

- [ ] **Step 5: 提交**

```bash
git add app/api/chat.py tests/unit/test_sse_disconnect.py tests/integration/test_sse_disconnect.py
git commit -m "feat(sse-disconnect): background completion via queue+producer, in-flight registry, /generating endpoint"
```

---

### Task 3: 服务关停清理（lifespan）

**Files:**
- Modify: `app/main.py:22-63`

- [ ] **Step 1: 实现**（lifespan `yield` 后取消后台任务）

`app/main.py` 顶部 import 加一行：

```python
from app.api.chat import shutdown_in_flight_generations
```

lifespan 末尾 `yield` 之后追加：

```python
    yield
    logger.info("RAGent-py shutting down: cancelling in-flight background generations")
    await shutdown_in_flight_generations()
```

- [ ] **Step 2: 验证**

```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"
```
Expected: 无异常（import 链 + lifespan 语法）。

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_sse_disconnect.py::test_shutdown_cancels_background -q
```
Expected: PASS。

- [ ] **Step 3: 提交**

```bash
git add app/main.py
git commit -m "feat(sse-disconnect): cancel in-flight generations on lifespan shutdown"
```

---

### Task 4: memory get_history 排除 interrupted

**Files:**
- Modify: `app/core/memory.py:114-128`

- [ ] **Step 1: 实现**（`get_history` 过滤条件 + docstring）

```python
        """Return recent messages within token budget (history_max_tokens).

        DB 侧按 id 倒序取最新 _HISTORY_SCAN_LIMIT 条，再按预算从新往旧累加——
        不再全表加载；按 id（单调）排序，消除 created_at 同值并列隐患。
        空内容与 streaming/interrupted 状态消息不进上下文（半截回答不污染 LLM）。
        """
```

过滤行改为：

```python
            if not m.content or m.status in ("streaming", "interrupted"):
                continue
```

- [ ] **Step 2: 运行确认 green**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_sse_disconnect.py::test_interrupted_message_excluded_from_history -q
```
Expected: PASS（Task 1 已落该测试）。

- [ ] **Step 3: 提交**

```bash
git add app/core/memory.py
git commit -m "fix(memory): exclude interrupted messages from LLM context"
```

---

### Task 5: 前端禁发 + 有边界轮询

**Files:**
- Modify: `frontend/src/api/chat.ts`
- Modify: `frontend/src/views/ChatView.vue`

- [ ] **Step 1: 实现 `chat.ts`**（`getMessages` 之后加两个方法）

```typescript
  async generating(conversationId: string): Promise<{ generating: boolean }> {
    const res = await api.get(`/chat/conversations/${conversationId}/generating`)
    return res.data
  },
  async cancelGeneration(conversationId: string): Promise<{ cancelled: boolean }> {
    const res = await api.post(`/chat/conversations/${conversationId}/cancel`)
    return res.data
  },
```

- [ ] **Step 2: 实现 `ChatView.vue` script**（`let abortController` 之后加状态与函数）

```typescript
// sse-disconnect-continue：后台生成任务追踪（断开后后端继续跑完）
const bgConvs = ref<Set<string>>(new Set())
let bgPollTimer: ReturnType<typeof setInterval> | null = null

function isBgGenerating(): boolean {
  return currentConvId.value !== null && bgConvs.value.has(currentConvId.value)
}

function stopBgPoll() {
  if (bgPollTimer !== null) {
    clearInterval(bgPollTimer)
    bgPollTimer = null
  }
}

async function checkBgStatus() {
  for (const cid of [...bgConvs.value]) {
    try {
      const { generating } = await chatApi.generating(cid)
      if (!generating) {
        bgConvs.value = new Set([...bgConvs.value].filter((x) => x !== cid))
        if (currentConvId.value === cid) {
          await loadMessages(cid)   // 后台跑完：把完整答案刷出来
        }
      }
    } catch {
      /* 网络抖动：下轮轮询再试 */
    }
  }
  if (bgConvs.value.size === 0 && bgPollTimer !== null) {
    clearInterval(bgPollTimer)
    bgPollTimer = null
  }
}

function startBgPoll(convId: string) {
  bgConvs.value = new Set(bgConvs.value).add(convId)
  if (bgPollTimer === null) {
    bgPollTimer = setInterval(checkBgStatus, 2000)
  }
  void checkBgStatus()
}

async function refreshBgStatus(cid: string) {
  // 进入会话时探测一次：后台任务可能跨组件生命周期存活（刷新/重挂载）
  try {
    const { generating } = await chatApi.generating(cid)
    if (generating) {
      bgConvs.value = new Set(bgConvs.value).add(cid)
      if (bgPollTimer === null) {
        bgPollTimer = setInterval(checkBgStatus, 2000)
      }
      await checkBgStatus()
    }
  } catch {
    /* ignore */
  }
}
```

- [ ] **Step 3: 改 `abortStream` / 新增 `stopGeneration` / `send` / watch / onMounted / onUnmounted**

```typescript
function abortStream(startBg = true) {
  if (abortController) {
    abortController.abort()
    abortController = null
    // 默认断开后后端后台跑完：记录后台会话，同会话禁发 + 有边界轮询
    if (startBg && currentConvId.value) {
      startBgPoll(currentConvId.value)
    }
  }
  streamError.value = false
  streaming.value = false
}

async function stopGeneration() {
  // 「停止」按钮 = 真正取消（区别于离开的后台跑完）：调后端 cancel 端点
  const cid = currentConvId.value
  if (cid) {
    try { await chatApi.cancelGeneration(cid) } catch { /* 已断开/无任务：忽略 */ }
  }
  abortStream(false)   // 不启动后台轮询：已显式取消
}
```

`send()` 守卫第一行改为：

```typescript
  if (!q || streaming.value || isBgGenerating()) return
```

conv-switch watch 里 `if (id) {` 分支改为：

```typescript
    if (id) {
      await loadMessages(id)
      await refreshBgStatus(id)
    } else {
      messages.value = []
    }
```

`onMounted` 改为：

```typescript
onMounted(async () => {
  if (props.currentConvId) {
    currentConvId.value = props.currentConvId
    await loadMessages(props.currentConvId)
    await refreshBgStatus(props.currentConvId)
  }
})
```

`onUnmounted` 改为：

```typescript
onUnmounted(() => {
  abortStream()
  stopBgPoll()
})
```

- [ ] **Step 4: 改 template**——发送按钮禁用条件 + 后台生成提示

```html
    <div class="chat-input-area">
      <div v-if="isBgGenerating()" class="bg-generating-hint">上一轮回答仍在后台生成，完成后将自动显示…</div>
      <div class="chat-input-wrapper">
        <textarea
          v-model="input"
          class="chat-input"
          rows="1"
          placeholder="请输入问题..."
          @keydown="handleKeydown"
          @input="autoResize"
        />
        <button v-if="streaming" class="send-btn stop-btn" @click="stopGeneration" title="停止生成">
          <span class="stop-label">停止</span>
        </button>
        <button class="send-btn" :disabled="!input.trim() || streaming || isBgGenerating()" @click="send">
```

scoped style 里（`.chat-input-area` 附近）加：

```css
.bg-generating-hint {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 4px 2px 6px;
}
.stop-btn { background: var(--danger, #e5484d); }
.stop-label { font-size: 12px; }
```

- [ ] **Step 5: 构建验证**

```bash
cd frontend && npm run build
```
Expected: vue-tsc 类型检查 + vite build 通过，无 .js 产物污染（P3-17 已开 noEmit）。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/chat.ts frontend/src/views/ChatView.vue
git commit -m "feat(frontend): block same-conversation send while background generation runs, bounded /generating polling"
```

---

### Task 6: 全量回归 + 运行时实测

- [ ] **Step 1: 全量单测 + 集成**

```bash
D:/miniConda/envs/rag/python.exe -m pytest -q
```
Expected: 全量 PASS（含既有 211 例，无回归）。

- [ ] **Step 2: import 链检查**

```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"
```
Expected: 无异常。

- [ ] **Step 3: 运行时实测**（docker compose up -d 起 PG 后）

```bash
D:/miniConda/envs/rag/python.exe -m app.main        # 终端 1：后端
cd frontend && npm run dev                            # 终端 2：前端 → http://localhost:5173
```

操作路径：
1. 登录 → 发一条问题 → 等 token 流出 → **切换到另一个会话**（触发断开）。
2. 后端日志确认：无 CancelledError/异常，后台任务继续跑完。
3. 立即切回原会话：应看到「后台生成中」提示、发送按钮禁用。
4. 等几秒（轮询 2s）→ 完整回答自动刷出 → 提示消失、发送恢复可用。
5. 再发一条问题 → 流出几个 token 后点**「停止」按钮** → 流立即停止、无后台提示（已真正取消）→ 立即可发新消息。
6. **直接关闭浏览器标签页**（后台生成中）→ 重新打开 http://localhost:5173 → 进入该会话 → 完整答案已在。
7. 刷新页面回到该会话 → 完整答案仍在（已落库）。
8. 回归：发消息 → 自然跑完 → 正常收 done。

Expected: 8 步全部符合；后端日志无「后台生成任务异常」。

- [ ] **Step 4: 更新 plan 状态 + 提交收尾**

```bash
git add docs/plans/2026-08-12-sse-disconnect-continue.md docs/plans/README.md
git commit -m "docs(plan): mark sse-disconnect-continue complete"
```

（完成后把 README 索引「进行中」条目移到「已完成」并补 commit hash。）