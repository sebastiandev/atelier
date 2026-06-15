"""OpenCode CLI transcript catch-up via ``opencode export``.

OpenCode's export is the documented session surface: ``{info, messages}``
where each message carries ``{info?, role, time, ...}`` plus ``parts``
(``text`` / ``reasoning`` / ``tool`` / ``step-start`` / ``step-finish``).
No private DB files are read. Cursor is a message count, like Amp.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.infrastructure.agents.tool_canonical import canonicalize_tool
from src.infrastructure.cli_transcript.common import now_iso

_log = logging.getLogger(__name__)
_EXPORT_TIMEOUT_S = 30.0


def cursor_at_detach(session_id: str, workdir: Path) -> dict[str, Any]:
    export = fetch_export(session_id, workdir)
    count = len((export or {}).get("messages") or [])
    return {"provider": "opencode", "message_count": count}


def empty_cursor() -> dict[str, Any]:
    return {"provider": "opencode", "message_count": 0}


def transcript_path(_session_id: str, _workdir: Path) -> None:
    return None


def merge(
    session_id: str, workdir: Path, cursor: dict[str, Any]
) -> list[dict[str, Any]]:
    export = fetch_export(session_id, workdir)
    if export is None:
        return []
    messages = export.get("messages") or []
    start = int(cursor.get("message_count") or 0)
    events: list[dict[str, Any]] = []
    for msg in messages[start:]:
        if isinstance(msg, dict):
            events.extend(_translate_message(msg))
    return events


def fetch_export(session_id: str, workdir: Path) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            ["opencode", "export", session_id],
            capture_output=True,
            timeout=_EXPORT_TIMEOUT_S,
            text=True,
            check=False,
            cwd=str(workdir),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("opencode export failed for %s: %s", session_id, exc)
        return None
    if result.returncode != 0:
        _log.warning(
            "opencode export returned %s for %s: %s",
            result.returncode,
            session_id,
            (result.stderr or "").strip(),
        )
        return None
    try:
        parsed = json.loads(result.stdout)
    except ValueError as exc:
        _log.warning("opencode export gave non-JSON for %s: %s", session_id, exc)
        return None
    return parsed if isinstance(parsed, dict) else None


def _translate_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    raw_info = msg.get("info")
    info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else msg
    role = info.get("role")
    ts = _message_ts(info)
    parts = msg.get("parts") or []
    events: list[dict[str, Any]] = []
    if role == "user":
        texts = [
            str(p.get("text"))
            for p in parts
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        ]
        if texts:
            events.append({"type": "user_input", "ts": ts, "text": "\n\n".join(texts)})
        return events
    if role != "assistant":
        return events
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text" and part.get("text"):
            events.append(
                {"type": "message_complete", "ts": ts, "text": str(part["text"])}
            )
        elif part_type == "reasoning" and part.get("text"):
            events.append(
                {"type": "thinking_complete", "ts": ts, "text": str(part["text"])}
            )
        elif part_type == "tool":
            events.extend(_translate_tool_part(part, ts))
    return events


def _translate_tool_part(part: dict[str, Any], ts: str) -> list[dict[str, Any]]:
    raw_state = part.get("state")
    state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
    tool_id = str(part.get("callID") or part.get("id") or "")
    state_input = state.get("input")
    raw_input: dict[str, Any] = state_input if isinstance(state_input, dict) else {}
    name, args = canonicalize_tool(str(part.get("tool") or "tool"), raw_input)
    events: list[dict[str, Any]] = [
        {"type": "tool_call", "ts": ts, "tool_id": tool_id, "name": name, "arguments": args}
    ]
    output = state.get("output")
    status = state.get("status")
    if output is not None or status in ("completed", "error"):
        events.append(
            {
                "type": "tool_result",
                "ts": ts,
                "tool_id": tool_id,
                "content": output if isinstance(output, str) else json.dumps(output),
                "is_error": status == "error",
            }
        )
    return events


def _message_ts(info: dict[str, Any]) -> str:
    time_obj = info.get("time")
    created = time_obj.get("created") if isinstance(time_obj, dict) else None
    if isinstance(created, int | float):
        return datetime.fromtimestamp(created / 1000.0, UTC).isoformat()
    return now_iso()
