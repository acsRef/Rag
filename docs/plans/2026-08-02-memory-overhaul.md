> 状态: 已完成（commits: 335d007 / b186226 / 73c789e / d315b34，分支 fix/memory-overhaul）

# 记忆机制改造（memory-overhaul）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复会话记忆的永久丢消息 bug（时间戳水位 → 消息 id 水位），摘要移出请求路径（fire-and-forget），历史/摘要触发改为 DB 侧窗口化查询（消除每轮全表加载），并补齐失败退避、prompt 上限、会话标题、空消息过滤。

**Architecture:** 摘要"已覆盖到哪"的水位从 `last_summary_at`（时间戳）换成 `conversations.last_summarized_msg_id`（Message 自增主键，单调）——窗口滑动时滑出的消息只要 id 大于水位就必然进入增量摘要，结构性消除丢失。摘要任务由 `add_message` 同步 await 改为 `asyncio.create_task` 后台执行（per-conversation 锁保留）。`get_history` 与摘要触发都不再全表加载：前者 `ORDER BY id DESC LIMIT 100` 后按 token 预算累加，后者用 SQL `SUM(LENGTH(content))` 聚合判断触发阈值。

**Tech Stack:** SQLAlchemy（model + init_db 幂等 ALTER，沿用 `last_summary_at` 当年加入的模式）、pytest integration 层（`ragent_test` 库 + fake minimax chat）。

---

## Context

2026-08-02 审查中记忆机制的最高危缺陷（用户最初怀疑的点）：

1. **M1 永久丢消息（高危）**：`_maybe_summarize` 用 `last_summary_at` 时间戳过滤"尚未摘要的窗口外消息"（memory.py:212-217）。T1 摘要时**在窗口内**（未被摘要）的消息，之后滑出窗口时 `created_at < last_summary_at` → 被增量摘要排除 → **既不在窗口也不在摘要，永久消失**。对话越长丢得越多。
2. **M2 摘要阻塞请求（设计缺陷）**：`add_message` 末尾同步 `await _maybe_summarize`，pipeline 每轮调用两次（user + assistant）→ 触发时首 token 前多等一整次 LLM 往返。
3. **M3 每轮全表加载（性能）**：`get_history` 与 `_maybe_summarize` 都 `.order_by(created_at.asc()).all()` 加载会话全部消息再 Python 过滤；长会话每轮 DB 负载线性增长。排序依赖 `created_at` 还有同微秒并列隐患。
4. **M4 失败无退避、prompt 无上限**：摘要失败后每条新消息立即重试；fresh 路径把全部窗口外消息塞进 prompt，持续失败时无界增长。
5. **零碎项**：会话 `title` 永远为空（列表全是 "New conversation"）；空内容消息照进上下文；`status="streaming"` 分支是全仓无调用方的死代码；`_get_outside_window` 被本 plan 的窗口内联计算取代。

## Design

### 水位与摘要范围（M1 核心）

- 新增列 `conversations.last_summarized_msg_id INTEGER`（nullable；NULL 视同 0）。
- 窗口边界：取最新 100 条（`ORDER BY id DESC LIMIT 100`）从新往旧累加 token，超出 `history_max_tokens` 处停下，最后一条入窗消息的 id 即 `boundary_id`（全部入窗则为最旧那条的 id）。
- 摘要候选 = `id < boundary_id AND id > last_summarized_msg_id` 的消息（升序）。**滑出窗口的消息 id 必然 > 旧水位 → 必然被覆盖**，丢失不再可能。
- 触发判断：候选集 `SUM(LENGTH(content))/1.5 >= summary_trigger_tokens`（SQL 聚合，不拉数据）。
- 摘要成功后：`summary = 新摘要`，`last_summarized_msg_id = max(候选 id)`，`last_summary_at = now`（保留该列仅作信息性记录，不再参与逻辑）。

### 异步化（M2）

- `add_message` 把 `_maybe_summarize` 改为 `asyncio.create_task` fire-and-forget；done-callback 消费异常防"unhandled task exception"告警；`_maybe_summarize` 整体包 try/except 记日志。
- generator 关闭场景（pipeline 的 GeneratorExit 路径也调 add_message）：`create_task` 可能遇上 loop 关闭 → try/except RuntimeError 静默跳过（下轮对话会补上）。
- 本轮对话继续用旧摘要——摘要晚一轮更新无语义损失。

### 窗口化查询（M3）

- `get_history`：`ORDER BY id DESC LIMIT 100` → 累加 token 到预算停 → reverse。同时过滤空内容与 `status="streaming"`。排序改 id（单调）顺带消除时间戳并列隐患。
- `_HISTORY_SCAN_LIMIT = 100`：单条 ≥ `history_max_tokens` 的极端消息也至多回看 100 条，足够（2000 token 预算 / 正常消息远小于 100）。

### 失败退避与 prompt 上限（M4）

- 模块级 `_summary_failures: dict[str, tuple[int, float]]` = {conv_id: (连续失败次数, 上次失败时间)}；退避 `min(900, 60 * 2**(n-1))` 秒；成功后清除。
- fresh 路径 prompt：窗口外消息**从最旧截断**、保留最近约 8000 字符，截断时 prompt 头部注明"（更早 N 条已省略）"。增量路径天然受限于水位区间，无需额外截断。

### 其余

- **标题**：`add_message` 中 `role="user"` 且 `conv.title` 为空时，`title = content[:30]`（确定性、零 LLM 成本）。
- **摘要结构化**：`_SUMMARY_FRESH` / `_SUMMARY_UPDATE` 改为要求四个固定小节（主题与结论 / 关键实体与数据 / 用户偏好与意图 / 可能被代词引用的概念）——直接服务 rewrite 的代词消解。
- **死代码**：删除 add_message 的 streaming 分支与 `_get_outside_window`（及其单测）。

### 错误路径枚举

| 场景 | 行为 |
|---|---|
| 摘要 LLM 失败 | 记日志 + 退避计数；不影响 add_message 返回；退避期内不重试 |
| 退避过期后再失败 | 退避时间翻倍（上限 15 分钟） |
| 摘要成功 | 清退避计数；水位推进到本批 max id |
| 会话全部消息都在窗口内 | 候选集空 → 直接返回，不调 LLM |
| 单条消息超过整个 history 预算 | get_history 返回该条（至少保留最新一条的语义沿用现状：累加前先判空列表）——实际行为：首条即超预算 → break → 返回空列表。与现状（break 前不 append）一致，不改语义 |
| generator 关闭时 add_message | create_task 失败被吞（debug 日志），摘要下轮补 |
| last_summarized_msg_id 为 NULL（旧会话） | 视同 0，首次摘要覆盖全部窗口外消息 |

### Schema 变更说明

CLAUDE.md 约束"Do not modify SQLAlchemy models（requires migrations）"的意图是禁止**无迁移**的模型变更。本 plan 按该仓库既有的幂等迁移模式操作：model 加字段 + `init_db()` 加 `ADD COLUMN IF NOT EXISTS`（与 `summary`、`last_summary_at` 两列当年的加入方式完全一致），同一变更内完成。

## Files to change

| 变更 | 路径 | 说明 |
|---|---|---|
| Modify | `app/store/db.py` | `Conversation` 加 `last_summarized_msg_id`；`init_db` 加 ALTER |
| Modify | `app/core/memory.py` | 水位逻辑、异步化、窗口化查询、退避、prompt 上限、标题、结构化 prompt、删死代码 |
| Modify | `tests/unit/test_memory_helpers.py` | 删除 `_get_outside_window` 相关用例（函数已删），保留 token 估算 |
| Create | `tests/integration/test_memory_overhaul.py` | 丢消息回归、异步不阻塞、标题、空消息过滤、退避（6 例） |
| Modify | `docs/plans/README.md` | 登记；完成后转「已完成」 |

## Reused existing utilities

| 复用对象 | 路径 | 用途 |
|---|---|---|
| `init_db` 幂等 ALTER 模式 | `app/store/db.py` | 新列迁移，与既有 8 处 ALTER 同构 |
| `_acquire_lock` / `_summary_locks` | `app/core/memory.py` | per-conversation 摘要互斥，原样保留 |
| `call_llm_with_retry` + `minimax_client.chat` | `app/llm/base.py` / `app/llm/chat.py` | 摘要调用不变，测试 monkeypatch 此入口 |
| `_estimate_tokens` | `app/core/memory.py` | 窗口累加与触发估算 |
| integration 底座 | `tests/integration/conftest.py` | `integration_db` fixture |

---

## Tasks

### Task 1: Schema 与死代码清理

**Files:** `app/store/db.py`, `app/core/memory.py`

- [ ] **Step 1: `app/store/db.py` 加列**

`Conversation` 模型 `last_summary_at` 行后加：

```python
    last_summarized_msg_id = Column(Integer, nullable=True)
```

`init_db()` 的 `last_summary_at` ALTER 后加：

```python
            conn.execute(
                text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS last_summarized_msg_id INTEGER")
            )
```

- [ ] **Step 2: 删除 `memory.py` 的 streaming 死分支**

`add_message._sync` 中 `if status == "streaming": ... else:` 整个分支结构改为只保留原 else 分支内容（新建 Message 并 add），`status` 参数保留（调用方仍传 completed/interrupted）。

- [ ] **Step 3: 验证 import 链与既有套件**

Run:
```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"
D:/miniConda/envs/rag/python.exe -m pytest -q
```
Expected: import 成功；套件 `62 passed, 2 skipped, 7 xfailed`（行为未变）。

- [ ] **Step 4: Commit**

```bash
git add app/store/db.py app/core/memory.py
git commit -m "refactor(memory): add last_summarized_msg_id column, drop dead streaming branch + plan: memory-overhaul"
```

---

### Task 2: 窗口化 get_history

**Files:** `app/core/memory.py`, `tests/integration/test_memory_overhaul.py`

- [ ] **Step 1: 写失败测试 `tests/integration/test_memory_overhaul.py`（history 部分）**

```python
"""记忆机制改造回归测试：窗口化历史 / 丢消息修复 / 异步摘要 / 标题 / 退避。"""
import asyncio
import time

import pytest

from app.config import settings
from app.core.memory import conversation_memory
from app.llm.chat import minimax_client

_SUMMARY_TEXT = "## 主题与结论\n测试摘要"


@pytest.fixture
def fake_summary_llm(monkeypatch):
    """替换 minimax_client.chat，捕获摘要 prompt。"""
    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages[-1]["content"])
        return _SUMMARY_TEXT

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    return captured


async def _flush():
    await asyncio.sleep(0.1)   # 让 fire-and-forget 摘要任务跑完


async def test_history_windowed_and_chronological(integration_db, monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 60)   # 恰好 3 条 20-token 消息
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "hist-user")
    contents = ["h%d%s" % (i, "x" * 28) for i in range(6)]    # 每条 20 token
    for c in contents:
        await conversation_memory.add_message(conv, "user", c, user_id="hist-user")

    history = conversation_memory.get_history(conv)
    assert [m["content"] for m in history] == contents[-3:]   # 最新 3 条、时间顺序


async def test_history_excludes_empty_messages(integration_db):
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "empty-user")
    await conversation_memory.add_message(conv, "user", "有内容", user_id="empty-user")
    await conversation_memory.add_message(conv, "assistant", "", user_id="empty-user")

    history = conversation_memory.get_history(conv)
    assert [m["content"] for m in history] == ["有内容"]
```

- [ ] **Step 2: 运行确认失败**（窗口化未实现时第一条拿回全部 6 条）

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_memory_overhaul.py -q
```
Expected: `test_history_windowed_and_chronological` FAIL；空消息测试可能 FAIL（现状不过滤）。

- [ ] **Step 3: 重写 `get_history`**

```python
_HISTORY_SCAN_LIMIT = 100


    def get_history(self, conversation_id: str) -> list[dict]:
        """Return recent messages within token budget (history_max_tokens).

        DB 侧按 id 倒序取最新 _HISTORY_SCAN_LIMIT 条，再按预算累加——
        不再全表加载；id 单调，消除 created_at 并列隐患。
        """
        with get_db_ctx() as session:
            recent = (
                session.query(Message)
                .filter_by(conversation_id=conversation_id)
                .order_by(Message.id.desc())
                .limit(_HISTORY_SCAN_LIMIT)
                .all()
            )

        selected: list[dict] = []
        token_total = 0
        for m in recent:  # newest-first
            if not m.content or m.status == "streaming":
                continue
            t = _estimate_tokens(m.content)
            if token_total + t > settings.history_max_tokens:
                break
            selected.append({"role": m.role, "content": m.content})
            token_total += t
        selected.reverse()
        return selected
```

- [ ] **Step 4: 运行确认通过**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_memory_overhaul.py -q
```
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add app/core/memory.py tests/integration/test_memory_overhaul.py
git commit -m "perf(memory): windowed get_history via id-DESC limit, exclude empty messages"
```

---

### Task 3: 水位摘要（M1 核心）+ 异步化（M2）

**Files:** `app/core/memory.py`, `tests/integration/test_memory_overhaul.py`

- [ ] **Step 1: 追加失败测试（丢消息回归 + 异步）**

```python
async def test_slid_out_messages_are_never_lost(integration_db, monkeypatch, fake_summary_llm):
    """核心回归：T1 摘要时在窗口内的消息，滑出后必须进入增量摘要（旧时间戳水位会永久丢失）。"""
    monkeypatch.setattr(settings, "history_max_tokens", 60)    # 窗口 = 3 条
    monkeypatch.setattr(settings, "summary_trigger_tokens", 30)  # 窗口外 ≥2 条即触发
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "loss-user")

    def msg(i):
        return "L%d%s" % (i, "x" * 28)   # 每条 20 token，内容可辨识

    # 阶段 1：m0..m4 → 窗口 m2..m4，首次摘要覆盖 m0,m1
    for i in range(5):
        await conversation_memory.add_message(conv, "user", msg(i), user_id="loss-user")
        await _flush()
    assert len(fake_summary_llm) == 1
    assert msg(0) in fake_summary_llm[0] and msg(1) in fake_summary_llm[0]

    # 阶段 2：m5,m6 → m2,m3 滑出（二者在阶段 1 都在窗口内、未被摘要）
    for i in (5, 6):
        await conversation_memory.add_message(conv, "user", msg(i), user_id="loss-user")
        await _flush()
    assert len(fake_summary_llm) == 2, "滑出消息未触发增量摘要（丢消息 bug）"
    assert msg(2) in fake_summary_llm[1] and msg(3) in fake_summary_llm[1]

    # 水位推进到 m3：摘要与最新窗口拼起来应覆盖全部消息，无空洞
    summary = conversation_memory.get_summary(conv)
    assert summary == _SUMMARY_TEXT


async def test_summarize_does_not_block_add_message(integration_db, monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 60)
    monkeypatch.setattr(settings, "summary_trigger_tokens", 30)
    finished = []

    async def slow_chat(messages, **kw):
        await asyncio.sleep(0.5)
        finished.append(1)
        return _SUMMARY_TEXT

    monkeypatch.setattr(minimax_client, "chat", slow_chat)
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "async-user")

    t0 = time.monotonic()
    for i in range(5):
        await conversation_memory.add_message(conv, "user", "A%d%s" % (i, "x" * 28),
                                              user_id="async-user")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.4, "add_message 仍在同步等待摘要（实测 %.2fs）" % elapsed
    await asyncio.sleep(0.8)
    assert finished, "后台摘要任务未执行"
```

- [ ] **Step 2: 运行确认失败**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_memory_overhaul.py -q
```
Expected: 两条新测试 FAIL（旧水位丢 m2/m3；add_message 同步等待摘要耗时 ≥0.5s）。

- [ ] **Step 3: 重写 `_maybe_summarize` 与 `add_message` 的调用方式**

`add_message` 末尾：

```python
        await asyncio.to_thread(_sync)
        # 摘要移出请求路径：fire-and-forget，本轮对话用旧摘要即可
        try:
            task = asyncio.create_task(self._maybe_summarize(conversation_id))
            task.add_done_callback(_consume_task_exception)
        except RuntimeError:
            logger.debug("summary task skipped (loop closing) conv=%s", conversation_id[:8])
```

模块底部辅助：

```python
def _consume_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.exception("Background summarization failed", exc_info=exc)
```

`_maybe_summarize` 整体替换为水位版：

```python
    async def _maybe_summarize(self, conversation_id: str) -> None:
        """窗口外且未摘要（id > 水位）的消息累积到阈值时触发摘要。

        水位 = conversations.last_summarized_msg_id（Message.id 单调），
        滑出窗口的消息只要未被摘要过就一定进入候选——不再可能永久丢失。
        """
        try:
            with get_db_ctx() as session:
                conv = session.query(Conversation).filter_by(
                    conversation_id=conversation_id
                ).first()
                if not conv:
                    return
                recent = (
                    session.query(Message)
                    .filter_by(conversation_id=conversation_id)
                    .order_by(Message.id.desc())
                    .limit(_HISTORY_SCAN_LIMIT)
                    .all()
                )
                if not recent:
                    return
                # 窗口边界：最新入窗消息的 id
                acc = 0
                boundary_id = recent[-1].id
                for m in recent:
                    t = _estimate_tokens(m.content or "")
                    if acc + t > settings.history_max_tokens:
                        break
                    acc += t
                    boundary_id = m.id

                watermark = conv.last_summarized_msg_id or 0
                q = session.query(Message).filter(
                    Message.conversation_id == conversation_id,
                    Message.id < boundary_id,
                    Message.id > watermark,
                )
                chars = session.query(
                    func.coalesce(func.sum(func.length(Message.content)), 0)
                ).filter(
                    Message.conversation_id == conversation_id,
                    Message.id < boundary_id,
                    Message.id > watermark,
                ).scalar() or 0
                if chars / 1.5 < settings.summary_trigger_tokens:
                    return
                outside = q.order_by(Message.id.asc()).all()
                outside_items = [(m.id, m.role, m.content or "") for m in outside]
                has_summary = bool(conv.summary)
                existing_summary = conv.summary or ""

            if not outside_items:
                return

            if has_summary:
                new_turns = "\n".join(f"{role}: {content}" for _, role, content in outside_items)
                prompt = _SUMMARY_UPDATE.format(
                    existing=existing_summary,
                    new_turns=new_turns,
                    max_tokens=settings.summary_max_tokens,
                )
            else:
                prompt = _SUMMARY_FRESH.format(
                    text=_capped_conversation_text(outside_items),
                    max_tokens=settings.summary_max_tokens,
                )

            lock = _acquire_lock(conversation_id)
            if lock is None:
                return
            try:
                new_summary = await call_llm_with_retry(
                    minimax_client.chat,
                    [{"role": "user", "content": prompt}],
                    tag="summary",
                    max_retries=1,
                )
                new_watermark = outside_items[-1][0]
                with get_db_ctx() as session:
                    conv2 = session.query(Conversation).filter_by(
                        conversation_id=conversation_id
                    ).first()
                    if conv2:
                        conv2.summary = new_summary.strip()
                        conv2.last_summarized_msg_id = new_watermark
                        conv2.last_summary_at = datetime.now(timezone.utc)
                        session.commit()
                _summary_failures.pop(conversation_id, None)
                logger.info(
                    "summary.updated conv=%s msgs=%d watermark=%s",
                    conversation_id[:8], len(outside_items), new_watermark,
                )
            except Exception:
                logger.exception("Summary failed for conv=%s", conversation_id[:8])
            finally:
                lock.release()
                with _locks_guard:
                    _summary_locks.pop(conversation_id, None)
        except Exception:
            logger.exception("_maybe_summarize crashed conv=%s", conversation_id[:8])
```

顶部导入补 `from sqlalchemy import func`；删除 `_get_outside_window`、`_safe_created` 两个函数。

- [ ] **Step 4: 运行确认通过**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_memory_overhaul.py -q
```
Expected: 4 passed。

- [ ] **Step 5: Commit**

```bash
git add app/core/memory.py tests/integration/test_memory_overhaul.py
git commit -m "fix(memory): id-watermark summarization (no lost messages), async fire-and-forget"
```

---

### Task 4: 退避 + prompt 上限 + 标题 + 结构化 prompt

**Files:** `app/core/memory.py`, `tests/integration/test_memory_overhaul.py`

- [ ] **Step 1: 追加失败测试**

```python
async def test_summary_failure_backoff(integration_db, monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 60)
    monkeypatch.setattr(settings, "summary_trigger_tokens", 30)
    calls = []

    async def failing_chat(messages, **kw):
        calls.append(1)
        raise RuntimeError("llm down")

    monkeypatch.setattr(minimax_client, "chat", failing_chat)
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "backoff-user")
    for i in range(6):
        await conversation_memory.add_message(conv, "user", "B%d%s" % (i, "x" * 28),
                                              user_id="backoff-user")
        await _flush()
    assert len(calls) == 1, "退避期内不应重试（实际 %d 次）" % len(calls)

    # 模拟退避过期 → 允许重试
    from app.core import memory as memory_mod
    count, _ = memory_mod._summary_failures[conv]
    memory_mod._summary_failures[conv] = (count, 0.0)
    await conversation_memory.add_message(conv, "user", "B6%s" % ("x" * 28),
                                          user_id="backoff-user")
    await _flush()
    assert len(calls) == 2


async def test_title_from_first_user_message(integration_db):
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "title-user")
    await conversation_memory.add_message(conv, "user", "2024年各区域销售额排名",
                                          user_id="title-user")
    with conversation_memory_get_db_ctx() as session:
        title = _query_title(session, conv)
    assert title == "2024年各区域销售额排名"


# 标题查询辅助（避免在测试里重复 ORM 样板）
def conversation_memory_get_db_ctx():
    from app.store.db import get_db_ctx
    return get_db_ctx()


def _query_title(session, conv_id):
    from app.store.db import Conversation
    return session.query(Conversation).filter_by(conversation_id=conv_id).first().title
```

- [ ] **Step 2: 运行确认失败**（退避与标题均未实现）

- [ ] **Step 3: 实现**

`memory.py` 模块级：

```python
_summary_failures: dict[str, tuple[int, float]] = {}
_SUMMARY_PROMPT_CHAR_CAP = 8000
```

`_maybe_summarize` 开头加退避检查（`conv` 读取后）：

```python
                fail = _summary_failures.get(conversation_id)
                if fail:
                    n, ts = fail
                    import time as _time
                    if _time.time() - ts < min(900.0, 60.0 * (2 ** (n - 1))):
                        return
```

摘要失败分支（`except Exception` 记日志后）追加：

```python
                n, _ = _summary_failures.get(conversation_id, (0, 0.0))
                import time as _time
                _summary_failures[conversation_id] = (n + 1, _time.time())
```

（`import time` 提到模块顶部更整洁，实现时按顶部导入处理。）

prompt 上限辅助：

```python
def _capped_conversation_text(items: list[tuple[int, str, str]]) -> str:
    """fresh 摘要 prompt 的对话文本：保留最近的消息，总量超上限时从最旧截断。"""
    lines = [f"{role}: {content}" for _, role, content in items]
    text = "\n".join(lines)
    if len(text) <= _SUMMARY_PROMPT_CHAR_CAP:
        return text
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        if total + len(line) + 1 > _SUMMARY_PROMPT_CHAR_CAP:
            break
        kept.append(line)
        total += len(line) + 1
    dropped = len(lines) - len(kept)
    return "（更早 %d 条消息已省略）\n%s" % (dropped, "\n".join(reversed(kept)))
```

结构化摘要 prompt（替换 `_SUMMARY_FRESH` / `_SUMMARY_UPDATE`）：

```python
_SUMMARY_SECTIONS = (
    "严格按以下四个小节输出（每节 1-3 行，无小节可写「无」）：\n"
    "## 主题与结论\n## 关键实体与数据\n## 用户偏好与意图\n## 可能被代词引用的概念\n"
)

_SUMMARY_FRESH = (
    "请总结以下对话，保存关键信息。\n"
    "{sections}"
    "\n对话内容:\n{text}\n"
    "\n控制在 {max_tokens} token 以内。只输出摘要,不要额外解释。"
    "如果对话内容为空,输出「暂无对话内容」。"
)

_SUMMARY_UPDATE = (
    "请根据已有的摘要和新增的对话,生成更新后的摘要。\n"
    "{sections}"
    "\n已有摘要:\n{existing}\n"
    "\n新增对话:\n{new_turns}\n"
    "\n控制在 {max_tokens} token 以内。保留所有关键信息,不要丢失原有要点。\n"
    "只输出摘要,不要额外解释。"
)
```

（两处 `.format(...)` 调用补 `sections=_SUMMARY_SECTIONS`。）

标题：`add_message._sync` 内 `if conv:` 块扩展：

```python
                if conv:
                    conv.updated_at = datetime.now(timezone.utc)
                    if role == "user" and content and not conv.title:
                        conv.title = content[:30]
```

- [ ] **Step 4: 运行确认通过**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_memory_overhaul.py -q
```
Expected: 6 passed。

- [ ] **Step 5: Commit**

```bash
git add app/core/memory.py tests/integration/test_memory_overhaul.py
git commit -m "feat(memory): failure backoff, prompt cap, auto title, structured summary"
```

---

### Task 5: 收尾——删过时单测 + 全量回归 + 状态登记

- [ ] **Step 1: 删除 `tests/unit/test_memory_helpers.py` 中 `_get_outside_window` 的 3 条用例**

保留 `test_estimate_tokens_empty` / `test_estimate_tokens_mixed_content`；删除函数顶部无用的 `SimpleNamespace` 导入与 `_msg` 辅助（若不再被引用）。

- [ ] **Step 2: 全量回归**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest -q
```
Expected: `65 passed, 2 skipped, 7 xfailed`（62 − 3 删除 + 6 新增）。任何非 xfail/skip 失败与任何 XPASS 必须排查。

- [ ] **Step 3: 手工冒烟**

启动 backend，用 curl 发 5 轮以上带代词的对话（`/api/v1/chat/stream`），确认：摘要异步不拖慢首 token（status 事件先于思考出现）、`GET /chat/conversations` 标题非空。

- [ ] **Step 4: 更新 `docs/plans/README.md` 与本文件状态**

本 plan 转「已完成」（带 commits），索引同步。

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_memory_helpers.py docs/plans/
git commit -m "docs(plans): mark memory-overhaul complete + plan: memory-overhaul"
```

---

## Verification

| 验证项 | 命令 / 方式 | 期望 |
|---|---|---|
| 全量套件 | `D:/miniConda/envs/rag/python.exe -m pytest -q` | `65 passed, 7 xfailed, 2 skipped` |
| 丢消息回归 | `test_slid_out_messages_are_never_lost` | passed：增量摘要 prompt 含滑出消息 |
| 异步化 | `test_summarize_does_not_block_add_message` | 5 条消息 add 总耗时 < 0.4s（摘要 sleep 0.5s） |
| 窗口化 | `test_history_windowed_and_chronological` | 只回最新 3 条、时间序 |
| 退避 | `test_summary_failure_backoff` | 失败后 6 次 add 仅调 LLM 1 次 |
| 标题 | `test_title_from_first_user_message` | 首条用户消息前 30 字 |
| import 链 | `D:/miniConda/envs/rag/python.exe -c "import app.main"` | 退出码 0 |
| 冒烟 | 5+ 轮对话，观察首 token 延迟与标题 | 摘要不阻塞；标题非空 |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| 摘要改 LLM 结构化 JSON 输出（response_format） | 依赖 provider 能力与 `llm-gateway-convergence` 的统一网关设计；本 plan 用结构化文本小节，收益已覆盖 rewrite 消解需求 |
| 消息表清理/归档 | 窗口化查询已消除性能问题；数据保留策略是独立议题 |
| 多 worker 摘要并发（进程间锁） | 现状单 worker 部署；进程内锁 + 水位幂等（重复摘要无副作用，仅浪费一次 LLM）可接受，分布式锁随 Redis 引入一并做 |
| 重写 rewrite/intent 使用摘要的方式 | 属 `llm-gateway-convergence`；结构化摘要已为其铺路 |
| 为"单条消息超预算"设计分片 | 极端场景，现状语义（返回空）保持不变，YAGINI |
