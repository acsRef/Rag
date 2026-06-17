from app.store.db import get_session, Message, Conversation, new_id
from app.config import settings
from datetime import datetime, timezone


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
        finally:
            session.close()


conversation_memory = ConversationMemory()
