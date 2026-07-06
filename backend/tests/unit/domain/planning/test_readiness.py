from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.domain.chatstore.dtos import ChatRecord
from src.domain.commands.planning import mark_ready
from src.domain.models import Chat
from src.domain.planning.readiness import (
    PLANNING_READINESS_OPTION,
    scan_text_for_planning_readiness,
)


class _ChatStoreStub:
    def __init__(self, record: ChatRecord | None) -> None:
        self.record = record
        self.saved: tuple[str, str, Any] | None = None

    def get_chat(self, chat_slug: str) -> ChatRecord | None:
        if self.record is None or self.record.chat.slug != chat_slug:
            return None
        return self.record

    def set_chat_option(self, chat_slug: str, key: str, value: Any) -> None:
        assert self.record is not None
        self.saved = (chat_slug, key, value)
        self.record.chat.options = {**(self.record.chat.options or {}), key: value}


def test_scans_exact_planning_readiness_marker() -> None:
    readiness = scan_text_for_planning_readiness(
        'Ready.\n{"atelier_planning_ready":{"ready":true,"summary":"Enough"}}'
    )

    assert readiness is not None
    assert readiness.ready is True
    assert readiness.summary == "Enough"
    assert scan_text_for_planning_readiness("Ready to create source plan") is None


def test_mark_ready_persists_once_for_planning_chat() -> None:
    chat = _chat(title="Planning", grounding_ref="WRK-001")
    store = _ChatStoreStub(ChatRecord(chat=chat, transcript=[]))

    first = mark_ready.execute(
        store,
        mark_ready.MarkPlanningChatReadyRequest(
            chat_slug="CHT-001",
            assistant_text=(
                "Ready.\n"
                '{"atelier_planning_ready":{"ready":true,"summary":"Enough"}}'
            ),
        ),
    )
    second = mark_ready.execute(
        store,
        mark_ready.MarkPlanningChatReadyRequest(
            chat_slug="CHT-001",
            assistant_text='{"atelier_planning_ready":{"ready":true}}',
        ),
    )

    assert first is not None
    assert first.changed is True
    assert store.saved == (
        "CHT-001",
        PLANNING_READINESS_OPTION,
        {"ready": True, "summary": "Enough"},
    )
    assert second is not None
    assert second.changed is False


def test_mark_ready_ignores_non_planning_chat() -> None:
    store = _ChatStoreStub(
        ChatRecord(chat=_chat(title="General", grounding_ref="WRK-001"), transcript=[])
    )

    result = mark_ready.execute(
        store,
        mark_ready.MarkPlanningChatReadyRequest(
            chat_slug="CHT-001",
            assistant_text='{"atelier_planning_ready":{"ready":true}}',
        ),
    )

    assert result is None
    assert store.saved is None


def _chat(title: str, grounding_ref: str) -> Chat:
    now = datetime.now(UTC)
    return Chat(
        slug="CHT-001",
        title=title,
        provider="codex",
        model="gpt-5.4",
        grounding_kind="work",
        grounding_ref=grounding_ref,
        created_at=now,
        updated_at=now,
    )
