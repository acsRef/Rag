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

import logging
import threading

from app.store.db import get_db_ctx, Message, Conversation, new_id
from app.config import settings
from app.llm.chat import minimax_client
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
                if conv:
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

        Walks newest→oldest, accumulating token estimates, stops before
        exceeding the budget.  This replaces the old turn-based window.
        """
        with get_db_ctx() as session:
            all_msgs = (
                session.query(Message)
                .filter_by(conversation_id=conversation_id)
                .order_by(Message.created_at.asc())
                .all()
            )
        if not all_msgs:
            return []

        selected: list[dict] = []
        token_total = 0
        for m in reversed(all_msgs):  # newest-first scan
            t = _estimate_tokens(m.content or "")
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

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str = "",
        thinking_content: str | None = None,
        status: str = "completed",
        user_id: str = "default_user",
    ) -> None:
        with get_db_ctx() as session:
            conv = session.query(Conversation).filter_by(
                conversation_id=conversation_id
            ).first()
            # Upsert for streaming messages — same conversation+role, update in place
            if status == "streaming":
                existing = (
                    session.query(Message)
                    .filter_by(conversation_id=conversation_id, status="streaming")
                    .first()
                )
                if existing:
                    existing.content = content
                    existing.thinking_content = thinking_content
                    existing.metadata_json = existing.metadata_json or {}
            else:
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

        self._maybe_summarize(conversation_id)

    # ------------------------------------------------------------------
    # Auto-summarization — trigger by accumulated token overflow
    # ------------------------------------------------------------------

    def _maybe_summarize(self, conversation_id: str) -> None:
        """Check if old messages overflow the budget and trigger summarization.

        Old messages = those that fall OUTSIDE the recent-window budget.
        If their total estimated tokens exceed summary_trigger_tokens,
        invoke LLM to generate/update the summary.
        """
        with get_db_ctx() as session:
            msgs = (
                session.query(Message)
                .filter_by(conversation_id=conversation_id)
                .order_by(Message.created_at.asc())
                .all()
            )
            conv = session.query(Conversation).filter_by(
                conversation_id=conversation_id
            ).first()
            if not conv or not msgs:
                return

        outside_msgs = _get_outside_window(msgs)
        if not outside_msgs:
            return

        token_outside = sum(_estimate_tokens(m.content or "") for m in outside_msgs)
        if token_outside < settings.summary_trigger_tokens:
            return

        # Build the appropriate prompt (fresh vs incremental update)
        if conv.summary:
            new_turns = "\n".join(
                f"{m.role}: {m.content}"
                for m in outside_msgs
                if (not conv.last_summary_at)
                or (m.created_at and m.created_at > conv.last_summary_at)
            )
            if not new_turns.strip():
                return
            prompt = _SUMMARY_UPDATE.format(
                existing=conv.summary,
                new_turns=new_turns,
                max_tokens=settings.summary_max_tokens,
            )
        else:
            conversation_text = "\n".join(
                f"{m.role}: {m.content}" for m in outside_msgs
            )
            prompt = _SUMMARY_FRESH.format(
                text=conversation_text,
                max_tokens=settings.summary_max_tokens,
            )

        # Per-conversation lock — at most one summarization at a time
        lock = _acquire_lock(conversation_id)
        if lock is None:
            return  # another thread is already summarizing

        try:
            new_summary = minimax_client.chat(
                [{"role": "user", "content": prompt}]
            )
            with get_db_ctx() as session:
                conv2 = (
                    session.query(Conversation)
                    .filter_by(conversation_id=conversation_id)
                    .first()
                )
                if conv2:
                    conv2.summary = new_summary.strip()
                    conv2.last_summary_at = datetime.now(timezone.utc)
                    session.commit()
            logger.info(
                "summary.updated conv=%s outside=%d tokens=%d",
                conversation_id[:8], len(outside_msgs), token_outside,
            )
        except Exception:
            logger.exception("Summary failed for conv=%s", conversation_id[:8])
        finally:
            lock.release()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_outside_window(all_msgs: list) -> list:
    """Return messages that fall outside the token-budget window.

    Walks newest→oldest, accumulating tokens; everything beyond the
    budget goes into the 'outside' bucket (candidates for summarization).
    """
    token_acc = 0
    for i in range(len(all_msgs) - 1, -1, -1):
        token_acc += _estimate_tokens(all_msgs[i].content or "")
        if token_acc > settings.history_max_tokens:
            return list(all_msgs[: i + 1])
    return []


def _acquire_lock(conversation_id: str) -> threading.Lock | None:
    with _locks_guard:
        if conversation_id not in _summary_locks:
            _summary_locks[conversation_id] = threading.Lock()
        lock = _summary_locks[conversation_id]
    if not lock.acquire(blocking=False):
        return None
    return lock


conversation_memory = ConversationMemory()
