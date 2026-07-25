"""Tests for shared provider-turn observation."""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.agents.turn_monitor import observe_turn

STARTED_AT = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("events", "now", "expected_seconds", "expected_pending"),
    [
        (
            [
                {"type": "user_input", "ts": STARTED_AT.isoformat()},
                {
                    "type": "permission_request",
                    "request_id": "one",
                    "ts": (STARTED_AT + timedelta(minutes=5)).isoformat(),
                },
                {
                    "type": "permission_request",
                    "request_id": "two",
                    "ts": (STARTED_AT + timedelta(minutes=10)).isoformat(),
                },
            ],
            STARTED_AT + timedelta(minutes=65),
            5 * 60,
            True,
        ),
        (
            [
                {"type": "user_input", "ts": STARTED_AT.isoformat()},
                {
                    "type": "permission_request",
                    "request_id": "one",
                    "ts": (STARTED_AT + timedelta(minutes=5)).isoformat(),
                },
                {
                    "type": "permission_request",
                    "request_id": "two",
                    "ts": (STARTED_AT + timedelta(minutes=10)).isoformat(),
                },
                {
                    "type": "permission_decision",
                    "request_id": "one",
                    "ts": (STARTED_AT + timedelta(minutes=35)).isoformat(),
                },
                {
                    "type": "permission_decision",
                    "request_id": "two",
                    "ts": (STARTED_AT + timedelta(minutes=45)).isoformat(),
                },
            ],
            STARTED_AT + timedelta(minutes=65),
            25 * 60,
            False,
        ),
        (
            [
                {"type": "user_input", "ts": STARTED_AT.isoformat()},
                {
                    "type": "user_input",
                    "ts": (STARTED_AT + timedelta(minutes=60)).isoformat(),
                },
            ],
            STARTED_AT + timedelta(minutes=65),
            5 * 60,
            False,
        ),
    ],
)
def test_observation_excludes_permission_waits(
    events: list[dict[str, object]],
    now: datetime,
    expected_seconds: float,
    expected_pending: bool,
) -> None:
    observation = observe_turn(events, now)  # type: ignore[arg-type]

    assert observation.elapsed_seconds == expected_seconds
    assert observation.waiting_permission is expected_pending
