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

_SUMMARY_FRESH = (
    "请总结以下对话,保存关键信息:\n"
    " - 讨论的核心主题和关键结论\n"
    " - 重要术语、技术名词、数据\n"
    " - 用户的偏好和意图\n"
    " - 后续可能被代词引用(它、这个、那个、上面说的)的关键概念\n"
    "\n"
    "对话内容:\n"
    "{text}\n"
    "\n"
    "控制在 {max_tokens} token 以内。只输出摘要,不要额外解释。"
    "如果对话内容为空,输出「暂无对话内容」。"
)

_SUMMARY_UPDATE = (
    "请根据已有的摘要和新增的对话,生成更新后的摘要:\n"
    "\n"
    "已有摘要:\n"
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
        """窗口外且未摘要（id > 水位）的消息累积到阈值时触发摘要。

        水位 = conversations.last_summarized_msg_id（Message.id 单调）。
        滑出窗口的消息只要没被摘要过（id > 水位）就一定进入候选——
        旧时间戳水位会永久丢失"摘要时还在窗口内"的消息，本实现结构性消除。
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
                    return
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
                return

            if has_summary:
                new_turns = "\n".join(
                    f"{role}: {content}" for _, role, content in outside_items
                )
                prompt = _SUMMARY_UPDATE.format(
                    existing=existing_summary,
                    new_turns=new_turns,
                    max_tokens=settings.summary_max_tokens,
                )
            else:
                conversation_text = "\n".join(
                    f"{role}: {content}" for _, role, content in outside_items
                )
                prompt = _SUMMARY_FRESH.format(
                    text=conversation_text,
                    max_tokens=settings.summary_max_tokens,
                )

            # Per-conversation lock — at most one summarization at a time
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


def _acquire_lock(conversation_id: str) -> Optional[threading.Lock]:
    with _locks_guard:
        if conversation_id not in _summary_locks:
            _summary_locks[conversation_id] = threading.Lock()
        lock = _summary_locks[conversation_id]
    if not lock.acquire(blocking=False):
        return None
    return lock


conversation_memory = ConversationMemory()
