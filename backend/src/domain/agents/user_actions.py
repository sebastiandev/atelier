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
from typing import Any, get_args

from src.domain.agents.events import PermissionDecisionValue


@dataclass(frozen=True)
class SendInput:
    text: str

    @classmethod
    def parse(cls, data: dict[str, Any]) -> SendInput | None:
        text = data.get("text")
        return cls(text=text) if isinstance(text, str) else None


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
            return cls(request_id=rid, decision=decision)
        return None


UserAction = SendInput | StopTurn | ResolvePermission


_PARSERS: dict[str, Callable[[dict[str, Any]], UserAction | None]] = {
    "input": SendInput.parse,
    "stop": StopTurn.parse,
    "permission": ResolvePermission.parse,
}


def parse_user_action(data: dict[str, Any]) -> UserAction | None:
    """Return a typed action for ``data``, or ``None`` if the type is
    unknown or the payload doesn't validate."""
    parser = _PARSERS.get(data.get("type"))
    return parser(data) if parser is not None else None


__all__ = [
    "ResolvePermission",
    "SendInput",
    "StopTurn",
    "UserAction",
    "parse_user_action",
]
