"""SQLAlchemy implementation of ChatRepository."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import Chat
from src.infrastructure.database.tables import chats_table


class SqlChatRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @contextmanager
    def _txn(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_chat(self, chat: Chat) -> Chat:
        with self._txn() as session:
            chat.slug = _placeholder_slug()
            session.add(chat)
            session.flush()
            assert chat.id is not None
            chat.slug = f"CHT-{chat.id:03d}"
        return chat

    def update_chat(self, chat: Chat) -> Chat:
        if chat.slug is None:
            raise ValueError("update_chat requires slug")
        with self._txn() as session:
            existing = session.execute(
                select(Chat).where(chats_table.c.slug == chat.slug)
            ).scalar_one_or_none()
            if existing is None:
                session.add(chat)
            else:
                existing.title = chat.title
                existing.provider = chat.provider
                existing.model = chat.model
                existing.grounding_kind = chat.grounding_kind
                existing.grounding_ref = chat.grounding_ref
                existing.working_directory = chat.working_directory
                existing.created_at = chat.created_at
                existing.updated_at = chat.updated_at
                existing.session_id = chat.session_id
                existing.promoted_to_work_slug = chat.promoted_to_work_slug
        return chat

    def get_chat_by_slug(self, slug: str) -> Chat | None:
        with self._txn() as session:
            return session.execute(
                select(Chat).where(chats_table.c.slug == slug)
            ).scalar_one_or_none()

    def list_chats(self) -> list[Chat]:
        with self._txn() as session:
            return list(session.execute(select(Chat)).scalars().all())

    def delete_chat(self, slug: str) -> None:
        with self._txn() as session:
            existing = session.execute(
                select(Chat).where(chats_table.c.slug == slug)
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)


def _placeholder_slug() -> str:
    return f"_pending_{uuid.uuid4().hex}"


__all__ = ["SqlChatRepository"]
