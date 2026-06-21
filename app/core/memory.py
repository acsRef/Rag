from app.store.db import get_session, Message, Conversation, new_id
from app.config import settings
from app.llm.chat import minimax_client
from datetime import datetime, timezone

SUMMARY_UPDATE_PROMPT = """You are a conversation summarizer. Given an existing summary and new conversation turns, produce an updated summary.

Existing summary:
{existing_summary}

New conversation turns:
{new_turns}

Instructions:
1. Merge the new information into the existing summary
2. Keep key technical terms, proper nouns, and decisions
3. Preserve the "active topics" and "key terms" sections
4. Keep the summary concise ({max_tokens} tokens max)
5. If the question contains pronouns（它/它们/这个/那个/其/上述等）, resolve them to concrete terms in the summary
6. Output ONLY the updated summary text, no explanations"""

SUMMARY_FRESH_PROMPT = """Summarize the following conversation. Keep:
- Key topics discussed and decisions made
- Important facts, technical terms, and proper nouns
- User preferences and intents
- Active topics at the end
- Key terms that might be referenced by pronouns later

Output format:
[summary text]

Active topics: topic1, topic2, ...
Key terms: term1, term2, ...

Conversation:
{conversation_text}

Keep the summary under {max_tokens} tokens. Output ONLY the summary."""


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
