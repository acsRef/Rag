from app.store.db import get_session, Message, Conversation, new_id
from app.config import settings
from app.llm.chat import minimax_client
from datetime import datetime, timezone

SUMMARY_UPDATE_PROMPT = """你是一个对话摘要助手。请根据已有的摘要和新的对话轮次，生成更新后的摘要。

已有的摘要：
{existing_summary}

新的对话轮次：
{new_turns}

要求：
1. 将新信息合并到已有摘要中，不要丢失原有重要内容
2. 保留关键的技术术语、专有名词、用户偏好和已做出的决策
3. 保持"活跃话题"和"关键术语"两部分
4. 控制摘要长度在 {max_tokens} token 以内
5. 如果后续对话可能用代词引用某个概念（它、它们、这个、那个、其、上述等），请确保该概念在摘要中有明确的术语名称，便于消歧
6. 只输出摘要文本，不要额外解释

输出格式示例：
用户询问了 Transformer 注意力机制的原理，已解释 QKV 计算方式。

活跃话题：注意力机制、Transformer、QKV
关键术语：self-attention, scaled dot-product, softmax"""

SUMMARY_FRESH_PROMPT = """请总结以下对话。需要保留以下内容：

- 讨论的关键主题和做出的决策
- 重要的事实、技术术语和专有名词
- 用户的偏好和意图
- 结尾处的活跃话题
- 后续可能被代词引用的关键术语

对话内容：
{conversation_text}

输出格式（控制在 {max_tokens} token 以内）：
[摘要正文]

活跃话题：话题1、话题2、...
关键术语：术语1、术语2、...

要求：
- 只输出摘要内容，不要额外解释
- 如果对话内容为空，直接输出"暂无对话内容"
- 不要编造对话中不存在的信息"""


class ConversationMemory:
    def get_or_create_conversation(self, conversation_id: str | None, user_id: str = "default_user") -> str:
        session = get_session()
        try:
            if conversation_id:
                conv = session.query(Conversation).filter_by(conversation_id=conversation_id).first()
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
        finally:
            session.close()

    def get_history(self, conversation_id: str, turns: int | None = None) -> list[dict]:
        if turns is None:
            turns = settings.history_keep_turns
        session = get_session()
        try:
            messages = (
                session.query(Message)
                .filter_by(conversation_id=conversation_id)
                .order_by(Message.created_at.desc())
                .limit(turns * 2)
                .all()
            )
            messages.reverse()
            return [{"role": m.role, "content": m.content} for m in messages]
        finally:
            session.close()

    def get_summary(self, conversation_id: str) -> str:
        session = get_session()
        try:
            conv = session.query(Conversation).filter_by(conversation_id=conversation_id).first()
            return conv.summary if conv else ""
        finally:
            session.close()

    def add_message(self, conversation_id: str, role: str, content: str, user_id: str = "default_user"):
        session = get_session()
        try:
            msg = Message(
                message_id=new_id(),
                conversation_id=conversation_id,
                user_id=user_id,
                role=role,
                content=content,
            )
            session.add(msg)
            conv = session.query(Conversation).filter_by(conversation_id=conversation_id).first()
            if conv:
                conv.updated_at = datetime.now(timezone.utc)
            session.commit()

            self._maybe_summarize(conversation_id, session)
        finally:
            session.close()

    def _maybe_summarize(self, conversation_id: str, session):
        total = session.query(Message).filter_by(conversation_id=conversation_id).count()
        keep_count = settings.history_keep_turns * 2
        if total <= keep_count + settings.history_summary_turns:
            return

        conv = session.query(Conversation).filter_by(conversation_id=conversation_id).first()
        if not conv:
            return

        older = (
            session.query(Message)
            .filter_by(conversation_id=conversation_id)
            .order_by(Message.created_at.asc())
            .limit(total - keep_count)
            .all()
        )

        if conv.summary:
            new_turns_text = "\n".join(
                f"{m.role}: {m.content}" for m in older
                if not conv.last_summary_at or (m.created_at and m.created_at > conv.last_summary_at)
            )
            if not new_turns_text.strip():
                return
            prompt = SUMMARY_UPDATE_PROMPT.format(
                existing_summary=conv.summary,
                new_turns=new_turns_text,
                max_tokens=settings.max_summary_tokens,
            )
        else:
            conversation_text = "\n".join(f"{m.role}: {m.content}" for m in older)
            prompt = SUMMARY_FRESH_PROMPT.format(
                conversation_text=conversation_text,
                max_tokens=settings.max_summary_tokens,
            )

        new_summary = minimax_client.chat([{"role": "user", "content": prompt}])

        conv.summary = new_summary.strip()
        conv.last_summary_at = datetime.now(timezone.utc)
        session.commit()

    def summarize(self, conversation_id: str):
        session = get_session()
        try:
            self._maybe_summarize(conversation_id, session)
        finally:
            session.close()


conversation_memory = ConversationMemory()
