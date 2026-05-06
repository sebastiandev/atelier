"""Parsing + dispatch round-trips for the UserAction taxonomy."""

from __future__ import annotations

from src.domain.agents import (
    ResolvePermission,
    SendInput,
    StopTurn,
    parse_user_action,
)


def test_parse_send_input() -> None:
    action = parse_user_action({"type": "input", "text": "hello"})
    assert action == SendInput(text="hello")


def test_parse_send_input_missing_text() -> None:
    assert parse_user_action({"type": "input"}) is None


def test_parse_send_input_non_string_text() -> None:
    assert parse_user_action({"type": "input", "text": 42}) is None


def test_parse_stop_turn() -> None:
    assert parse_user_action({"type": "stop"}) == StopTurn()


def test_parse_resolve_permission_allow() -> None:
    action = parse_user_action(
        {"type": "permission", "request_id": "req-1", "decision": "allow"}
    )
    assert action == ResolvePermission(request_id="req-1", decision="allow")


def test_parse_resolve_permission_allow_always() -> None:
    action = parse_user_action(
        {"type": "permission", "request_id": "req-9", "decision": "allow_always"}
    )
    assert action == ResolvePermission(request_id="req-9", decision="allow_always")


def test_parse_resolve_permission_deny() -> None:
    action = parse_user_action(
        {"type": "permission", "request_id": "req-2", "decision": "deny"}
    )
    assert action == ResolvePermission(request_id="req-2", decision="deny")


def test_parse_resolve_permission_unknown_decision_rejected() -> None:
    assert (
        parse_user_action(
            {"type": "permission", "request_id": "req-3", "decision": "maybe"}
        )
        is None
    )


def test_parse_resolve_permission_missing_request_id() -> None:
    assert (
        parse_user_action({"type": "permission", "decision": "allow"}) is None
    )


def test_parse_unknown_type_returns_none() -> None:
    assert parse_user_action({"type": "rewind"}) is None


def test_parse_missing_type_returns_none() -> None:
    assert parse_user_action({"text": "hello"}) is None
