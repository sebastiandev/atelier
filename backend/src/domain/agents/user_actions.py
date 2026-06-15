"""Typed user actions an agent's transport can deliver.

The WS endpoint receives JSON frames from the client; this module owns
the *meaning* of those frames. Each action class:

  - Carries its typed payload as a frozen dataclass.
  - Knows how to validate-and-build itself from a parsed dict via
    ``parse(data)``.

The ``_PARSERS`` registry maps the wire ``type`` field to the
appropriate ``parse`` classmethod. ``parse_user_action`` is the single
entry point: give it a parsed dict, get back a typed ``UserAction`` or
``None`` for unknown / malformed input.

Adding a new action type:
  1. Add a frozen ``@dataclass`` with its fields and a ``parse``
     classmethod.
  2. Register it in ``_PARSERS``.
  3. Add a ``case`` branch in
     ``domain/commands/agents/handle_user_action.execute``.

The transport (WS handler) doesn't change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast, get_args

from src.domain.agents.events import PermissionDecisionValue
from src.domain.models import Context, ContextType


@dataclass(frozen=True)
class SendInput:
    text: str
    # Mid-session attachments. Empty for plain messages. Connection-backed
    # entries (jira / sentry / honeycomb) carry a ``conn_id`` slug so the
    # backend knows which credentials to use; simple types (text / url /
    # file / agentout) leave it ``None``.
    contexts: tuple[Context, ...] = ()

    @classmethod
    def parse(cls, data: dict[str, Any]) -> SendInput | None:
        text = data.get("text")
        if not isinstance(text, str):
            return None
        raw_contexts = data.get("contexts")
        contexts: tuple[Context, ...] = ()
        if raw_contexts is not None:
            parsed = _parse_contexts(raw_contexts)
            if parsed is None:
                return None
            contexts = parsed
        return cls(text=text, contexts=contexts)


def _parse_contexts(raw: Any) -> tuple[Context, ...] | None:
    if not isinstance(raw, list):
        return None
    out: list[Context] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        ctype = item.get("type")
        value = item.get("value")
        if ctype not in get_args(ContextType) or not isinstance(value, str):
            return None
        conn_id = item.get("conn_id")
        if conn_id is not None and not isinstance(conn_id, str):
            return None
        out.append(
            Context(type=cast(ContextType, ctype), value=value, conn_id=conn_id)
        )
    return tuple(out)


@dataclass(frozen=True)
class StopTurn:
    @classmethod
    def parse(cls, data: dict[str, Any]) -> StopTurn | None:
        return cls()


@dataclass(frozen=True)
class ResolvePermission:
    request_id: str
    decision: PermissionDecisionValue

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ResolvePermission | None:
        rid = data.get("request_id")
        decision = data.get("decision")
        if isinstance(rid, str) and decision in get_args(PermissionDecisionValue):
            return cls(
                request_id=rid,
                decision=cast(PermissionDecisionValue, decision),
            )
        return None


@dataclass(frozen=True)
class SetSessionConfigOption:
    config_id: str
    value: str | bool

    @classmethod
    def parse(cls, data: dict[str, Any]) -> SetSessionConfigOption | None:
        config_id = data.get("config_id")
        value = data.get("value")
        if isinstance(config_id, str) and config_id:
            if isinstance(value, (str, bool)):
                return cls(config_id=config_id, value=value)
        return None


@dataclass(frozen=True)
class RefreshSessionConfigOptions:
    config_id: str

    @classmethod
    def parse(cls, data: dict[str, Any]) -> RefreshSessionConfigOptions | None:
        config_id = data.get("config_id")
        if isinstance(config_id, str) and config_id:
            return cls(config_id=config_id)
        return None


UserAction = (
    SendInput
    | StopTurn
    | ResolvePermission
    | SetSessionConfigOption
    | RefreshSessionConfigOptions
)


_PARSERS: dict[str, Callable[[dict[str, Any]], UserAction | None]] = {
    "input": SendInput.parse,
    "stop": StopTurn.parse,
    "permission": ResolvePermission.parse,
    "session_config": SetSessionConfigOption.parse,
    "session_config_refresh": RefreshSessionConfigOptions.parse,
}


def parse_user_action(data: dict[str, Any]) -> UserAction | None:
    """Return a typed action for ``data``, or ``None`` if the type is
    unknown or the payload doesn't validate."""
    action_type = data.get("type")
    if not isinstance(action_type, str):
        return None
    parser = _PARSERS.get(action_type)
    return parser(data) if parser is not None else None


__all__ = [
    "RefreshSessionConfigOptions",
    "ResolvePermission",
    "SendInput",
    "SetSessionConfigOption",
    "StopTurn",
    "UserAction",
    "parse_user_action",
]
