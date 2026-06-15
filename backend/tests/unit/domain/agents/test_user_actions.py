"""Parsing + dispatch round-trips for the UserAction taxonomy."""

from __future__ import annotations

from src.domain.agents import (
    RefreshSessionConfigOptions,
    ResolvePermission,
    SendInput,
    SetSessionConfigOption,
    StopTurn,
    parse_user_action,
)
from src.domain.models import Context


def test_parse_send_input() -> None:
    action = parse_user_action({"type": "input", "text": "hello"})
    assert action == SendInput(text="hello")


def test_parse_send_input_with_contexts() -> None:
    action = parse_user_action(
        {
            "type": "input",
            "text": "look at this",
            "contexts": [
                {"type": "text", "value": "a note"},
                {"type": "jira", "value": "ENG-1", "conn_id": "con-3"},
            ],
        }
    )
    assert action == SendInput(
        text="look at this",
        contexts=(
            Context(type="text", value="a note", conn_id=None),
            Context(type="jira", value="ENG-1", conn_id="con-3"),
        ),
    )


def test_parse_send_input_with_empty_contexts_list() -> None:
    action = parse_user_action(
        {"type": "input", "text": "plain", "contexts": []}
    )
    assert action == SendInput(text="plain", contexts=())


def test_parse_send_input_rejects_malformed_context_entry() -> None:
    """Wire-level validation: an entry missing required fields makes
    the whole frame unparseable rather than silently dropping the
    bad entry."""
    assert (
        parse_user_action(
            {
                "type": "input",
                "text": "x",
                "contexts": [{"type": "text"}],  # missing value
            }
        )
        is None
    )


def test_parse_send_input_rejects_unknown_context_type() -> None:
    assert (
        parse_user_action(
            {
                "type": "input",
                "text": "x",
                "contexts": [{"type": "rewind", "value": "v"}],
            }
        )
        is None
    )


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


def test_parse_set_session_config_option() -> None:
    action = parse_user_action(
        {"type": "session_config", "config_id": "model", "value": "opencode/gpt-5"}
    )
    assert action == SetSessionConfigOption(
        config_id="model", value="opencode/gpt-5"
    )


def test_parse_set_session_config_option_rejects_missing_value() -> None:
    assert parse_user_action({"type": "session_config", "config_id": "model"}) is None


def test_parse_refresh_session_config_options() -> None:
    action = parse_user_action(
        {"type": "session_config_refresh", "config_id": "model"}
    )
    assert action == RefreshSessionConfigOptions(config_id="model")


def test_parse_unknown_type_returns_none() -> None:
    assert parse_user_action({"type": "rewind"}) is None


def test_parse_missing_type_returns_none() -> None:
    assert parse_user_action({"text": "hello"}) is None
