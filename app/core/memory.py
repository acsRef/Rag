"""Token-budget conversation memory — sliding window + auto-summarization.

Design:
  - 所有消息入库(messages 表),不做截断
  - 每次构建上下文时,从最新→最旧逐条累加 token,到 budget 停止
  - 超出窗口的旧消息,累积一定 token 量后触发 LLM 摘要压缩
  - 摘要存于 conversations.summary 列,每条消息 token 数运行时按 len/1.5 估算(Chinese-mixed)

Token budget layout (config.py):
  system(prompt.py) → summary(800) → history(2000) → chunks(6000) → query(~700)
  Total capped at prompt_max_tokens(10000),超出时按 chunks→history→summary 倒序裁剪.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

from sqlalchemy import func

from app.store.db import get_db_ctx, Message, Conversation, new_id
from app.config import settings
from app.llm.chat import minimax_client
from app.llm.base import call_llm_with_retry
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_summary_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# 摘要失败退避：{conv_id: (连续失败次数, 上次失败时间)}
_summary_failures: dict[str, tuple[int, float]] = {}
_SUMMARY_PROMPT_CHAR_CAP = 8000   # fresh 摘要 prompt 的对话文本上限

_SUMMARY_SECTIONS = (
    "严格按以下四个小节输出（每节 1-3 行，无内容可写「无」）：\n"
    "## 主题与结论\n"
    "## 关键实体与数据\n"
    "## 用户偏好与意图\n"
    "## 可能被代词引用的概念\n"
)

_SUMMARY_FRESH = (
    "请总结以下对话,保存关键信息。\n"
    "{sections}"
    "\n对话内容:\n"
    "{text}\n"
    "\n"
    "控制在 {max_tokens} token 以内。只输出摘要,不要额外解释。"
    "如果对话内容为空,输出「暂无对话内容」。"
)

_SUMMARY_UPDATE = (
    "请根据已有的摘要和新增的对话,生成更新后的摘要。\n"
    "{sections}"
    "\n已有摘要:\n"
    "{existing}\n"
    "\n"
    "新增对话:\n"
    "{new_turns}\n"
    "\n"
    "控制在 {max_tokens} token 以内。保留所有关键信息,不要丢失原有要点。\n"
    "只输出摘要,不要额外解释。"
)


def _estimate_tokens(text: str) -> int:
    """Rough token estimator for mixed Chinese/English.

    Chinese ~1 char/token, English ~4 chars/token. We use 1.5 as the
    divisor — conservative for Chinese-heavy content.
    """
    if not text:
        return 0
    return max(1, int(len(text) / 1.5))


_HISTORY_SCAN_LIMIT = 100   # 窗口回看上限：预算内正常消息远少于此


class ConversationMemory:
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create_conversation(
        self, conversation_id: str | None, user_id: str = "default_user"
    ) -> str:
        with get_db_ctx() as session:
            if conversation_id:
                conv = session.query(Conversation).filter_by(
                    conversation_id=conversation_id
                ).first()
                # owner 校验：命中他人会话时静默新建，
                # 防止凭 conversation_id 读他人历史/摘要或向他人会话写入（IDOR）
                if conv and conv.user_id == user_id:
                    return conversation_id
            conv = Conversation(
                conversation_id=new_id(),
                user_id=user_id,
                title="",
            )
            session.add(conv)
            session.commit()
            return conv.conversation_id

    def get_history(self, conversation_id: str) -> list[dict]:
        """Return recent messages within token budget (history_max_tokens).

        DB 侧按 id 倒序取最新 _HISTORY_SCAN_LIMIT 条，再按预算从新往旧累加——
        不再全表加载；按 id（单调）排序，消除 created_at 同值并列隐患。
        空内容与 streaming 状态消息不进上下文。
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
        selected.reverse()  # back to chronological
        return selected

    def get_summary(self, conversation_id: str) -> str:
        with get_db_ctx() as session:
            conv = session.query(Conversation).filter_by(
                conversation_id=conversation_id
            ).first()
            return (conv.summary or "") if conv else ""

    def get_context(self, conversation_id: str) -> tuple[list[dict], str]:
        """Return (history_messages, summary) — ready for prompt injection."""
        return self.get_history(conversation_id), self.get_summary(conversation_id)

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str = "",
        thinking_content: str | None = None,
        status: str = "completed",
        user_id: str = "default_user",
    ) -> None:
        def _sync():
            with get_db_ctx() as session:
                conv = session.query(Conversation).filter_by(
                    conversation_id=conversation_id
                ).first()
                msg = Message(
                    message_id=new_id(),
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role=role,
                    content=content,
                    thinking_content=thinking_content,
                    status=status,
                )
                session.add(msg)
                if conv:
                    conv.updated_at = datetime.now(timezone.utc)
                    # 首条用户消息即标题（确定性、零 LLM 成本）
                    if role == "user" and content and not conv.title:
                        conv.title = content[:30]
                session.commit()
        await asyncio.to_thread(_sync)
        # 摘要移出请求路径：fire-and-forget，本轮对话用旧摘要即可
        try:
            task = asyncio.create_task(self._maybe_summarize(conversation_id))
            task.add_done_callback(_consume_task_exception)
        except RuntimeError:
            logger.debug("summary task skipped (loop closing) conv=%s", conversation_id[:8])

    # ------------------------------------------------------------------
    # Auto-summarization — trigger by accumulated token overflow
    # ------------------------------------------------------------------

    async def _maybe_summarize(self, conversation_id: str) -> None:
        """排水式摘要入口：循环直到不再触发摘要。

        摘要完成后 LLM 调用期间可能又累积了新消息，重新计算并继续，
        直到低于阈值——持锁任务一次收敛到位；未抢到锁的任务安全跳过
        （其覆盖区间由持锁者的排水过程兜底，不会丢消息）。
        """
        while await self._summarize_once(conversation_id):
            pass

    async def _summarize_once(self, conversation_id: str) -> bool:
        """单次摘要尝试：完成一次摘要返回 True（调用方继续排水），否则 False。"""
        try:
            # 失败退避：60s → 120s → … 上限 15 分钟
            fail = _summary_failures.get(conversation_id)
            if fail:
                n, ts = fail
                if time.time() - ts < min(900.0, 60.0 * (2 ** (n - 1))):
                    return False
            with get_db_ctx() as session:
                conv = session.query(Conversation).filter_by(
                    conversation_id=conversation_id
                ).first()
                if not conv:
                    return False
                recent = (
                    session.query(Message)
                    .filter_by(conversation_id=conversation_id)
                    .order_by(Message.id.desc())
                    .limit(_HISTORY_SCAN_LIMIT)
                    .all()
                )
                if not recent:
                    return False
                # 窗口边界：最新入窗消息的 id（全部入窗则为最旧那条）
                acc = 0
                boundary_id = recent[-1].id
                for m in recent:
                    t = _estimate_tokens(m.content or "")
                    if acc + t > settings.history_max_tokens:
                        break
                    acc += t
                    boundary_id = m.id

                watermark = conv.last_summarized_msg_id or 0
                # 触发判断走 SQL 聚合，不拉数据：字符数 / 1.5 ≈ token 估算
                chars = session.query(
                    func.coalesce(func.sum(func.length(Message.content)), 0)
                ).filter(
                    Message.conversation_id == conversation_id,
                    Message.id < boundary_id,
                    Message.id > watermark,
                ).scalar() or 0
                if chars / 1.5 < settings.summary_trigger_tokens:
                    return False
                outside = (
                    session.query(Message)
                    .filter(
                        Message.conversation_id == conversation_id,
                        Message.id < boundary_id,
                        Message.id > watermark,
                    )
                    .order_by(Message.id.asc())
                    .all()
                )
                outside_items = [(m.id, m.role, m.content or "") for m in outside]
                has_summary = bool(conv.summary)
                existing_summary = conv.summary or ""

            if not outside_items:
                return False

            if has_summary:
                new_turns = "\n".join(
                    f"{role}: {content}" for _, role, content in outside_items
                )
                prompt = _SUMMARY_UPDATE.format(
                    sections=_SUMMARY_SECTIONS,
                    existing=existing_summary,
                    new_turns=new_turns,
                    max_tokens=settings.summary_max_tokens,
                )
            else:
                prompt = _SUMMARY_FRESH.format(
                    sections=_SUMMARY_SECTIONS,
                    text=_capped_conversation_text(outside_items),
                    max_tokens=settings.summary_max_tokens,
                )

            # Per-conversation lock — at most one summarization at a time
            lock = _acquire_lock(conversation_id)
            if lock is None:
                return False

            try:
                new_summary = await call_llm_with_retry(
                    minimax_client.chat,
                    [{"role": "user", "content": prompt}],
                    tag="summary",
                    max_retries=1,
                )
                new_watermark = outside_items[-1][0]
                with get_db_ctx() as session:
                    conv2 = (
                        session.query(Conversation)
                        .filter_by(conversation_id=conversation_id)
                        .first()
                    )
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
                return True
            except Exception:
                logger.exception("Summary failed for conv=%s", conversation_id[:8])
                n, _ = _summary_failures.get(conversation_id, (0, 0.0))
                _summary_failures[conversation_id] = (n + 1, time.time())
                return False
            finally:
                lock.release()
                with _locks_guard:
                    _summary_locks.pop(conversation_id, None)
        except Exception:
            logger.exception("summarize crashed conv=%s", conversation_id[:8])
            return False


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _consume_task_exception(task: asyncio.Task) -> None:
    """fire-and-forget 摘要任务的 done-callback：消费异常，避免告警噪音。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Background summarization task failed: %r", exc)


def _capped_conversation_text(items: list[tuple[int, str, str]]) -> str:
    """fresh 摘要 prompt 的对话文本：总量超上限时从最旧截断、保留最近的消息。

    持续失败场景下 outside 集合会增长，此上限防止 prompt 无界膨胀。
    """
    lines = [f"{role}: {content}" for _, role, content in items]
    full = "\n".join(lines)
    if len(full) <= _SUMMARY_PROMPT_CHAR_CAP:
        return full
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        if total + len(line) + 1 > _SUMMARY_PROMPT_CHAR_CAP:
            break
        kept.append(line)
        total += len(line) + 1
    dropped = len(lines) - len(kept)
    return "（更早 %d 条消息已省略）\n%s" % (dropped, "\n".join(reversed(kept)))


def _acquire_lock(conversation_id: str) -> Optional[threading.Lock]:
    with _locks_guard:
        if conversation_id not in _summary_locks:
            _summary_locks[conversation_id] = threading.Lock()
        lock = _summary_locks[conversation_id]
    if not lock.acquire(blocking=False):
        return None
    return lock


conversation_memory = ConversationMemory()
