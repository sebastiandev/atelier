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


def _observe(events: list[dict[str, object]]) -> object:
    return observe_turn(events, STARTED_AT + timedelta(minutes=1))  # type: ignore[arg-type]


def test_provider_error_is_terminal() -> None:
    observation = _observe(
        [
            {"type": "user_input", "ts": STARTED_AT.isoformat()},
            {"type": "error", "message": "runtime died", "ts": STARTED_AT.isoformat()},
        ]
    )

    assert observation.terminal_error == "runtime died"  # type: ignore[attr-defined]


def test_recoverable_error_is_not_terminal() -> None:
    """A rejected artifact marker must not fail the surrounding loop stage."""
    observation = _observe(
        [
            {"type": "user_input", "ts": STARTED_AT.isoformat()},
            {
                "type": "error",
                "message": "invalid artifact marker: missing or empty 'title'",
                "recoverable": True,
                "ts": STARTED_AT.isoformat(),
            },
        ]
    )

    assert observation.terminal_error is None  # type: ignore[attr-defined]


def test_a_recoverable_error_does_not_mask_a_real_one() -> None:
    observation = _observe(
        [
            {"type": "user_input", "ts": STARTED_AT.isoformat()},
            {"type": "error", "message": "runtime died", "ts": STARTED_AT.isoformat()},
            {
                "type": "error",
                "message": "invalid artifact marker: nope",
                "recoverable": True,
                "ts": STARTED_AT.isoformat(),
            },
        ]
    )

    assert observation.terminal_error == "runtime died"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        pytest.param(
            [
                {"type": "user_input", "ts": STARTED_AT.isoformat()},
                {
                    "type": "tool_call",
                    "tool_id": "t1",
                    "ts": (STARTED_AT + timedelta(minutes=1)).isoformat(),
                },
            ],
            True,
            id="a started tool with no result is still running",
        ),
        pytest.param(
            [
                {"type": "user_input", "ts": STARTED_AT.isoformat()},
                {
                    "type": "tool_call",
                    "tool_id": "t1",
                    "ts": (STARTED_AT + timedelta(minutes=1)).isoformat(),
                },
                {
                    "type": "tool_result",
                    "tool_id": "t1",
                    "ts": (STARTED_AT + timedelta(minutes=2)).isoformat(),
                },
            ],
            False,
            id="a tool that reported its result is done",
        ),
        pytest.param(
            [
                {"type": "user_input", "ts": STARTED_AT.isoformat()},
                {
                    "type": "tool_call",
                    "tool_id": "t1",
                    "ts": (STARTED_AT + timedelta(minutes=1)).isoformat(),
                },
                {
                    "type": "tool_result",
                    "tool_id": "t1",
                    "ts": (STARTED_AT + timedelta(minutes=2)).isoformat(),
                },
                {
                    "type": "tool_call",
                    "tool_id": "t2",
                    "ts": (STARTED_AT + timedelta(minutes=3)).isoformat(),
                },
            ],
            True,
            id="only the unfinished tool of several counts",
        ),
        pytest.param(
            [{"type": "user_input", "ts": STARTED_AT.isoformat()}],
            False,
            id="no tool call at all",
        ),
    ],
)
def test_tool_in_flight_tracks_unfinished_tool_calls(
    events: list[dict[str, object]], expected: bool
) -> None:
    observation = observe_turn(events, STARTED_AT + timedelta(minutes=30))

    assert observation.tool_in_flight is expected


def test_a_long_silent_tool_call_is_not_an_idle_agent() -> None:
    """The WRK-014 case: a sub-agent exploration ran for five silent minutes
    and the monitor restarted a stage that was never stuck."""
    observation = observe_turn(
        [
            {"type": "user_input", "ts": STARTED_AT.isoformat()},
            {
                "type": "tool_call",
                "tool_id": "explore",
                "title": "Explore cycle-count LPN movement",
                "ts": (STARTED_AT + timedelta(seconds=48)).isoformat(),
            },
        ],
        STARTED_AT + timedelta(minutes=6),
    )

    assert observation.tool_in_flight is True
    assert observation.waiting_permission is False
