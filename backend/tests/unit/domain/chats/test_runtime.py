import asyncio
from typing import Any

from src.domain.chats.runtime import _close_interrupted_turn


class _ChatStore:
    def read_transcript_from_cursor(
        self, _chat_slug: str, _cursor: int
    ) -> Any:
        return iter(
            [
                {"type": "user_input", "text": "Continue"},
                {"type": "status_change", "status": "thinking"},
            ]
        )


class _Supervisor:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish_external_event(
        self, _chat_slug: str, event: dict[str, Any]
    ) -> bool:
        self.events.append(event)
        return True


def test_reconnect_closes_interrupted_chat_turn() -> None:
    supervisor = _Supervisor()

    asyncio.run(
        _close_interrupted_turn(  # type: ignore[arg-type]
            _ChatStore(), supervisor, "CHT-001"  # type: ignore[arg-type]
        )
    )

    assert [event["type"] for event in supervisor.events] == [
        "error",
        "status_change",
    ]
    assert supervisor.events[-1]["status"] == "idle"
