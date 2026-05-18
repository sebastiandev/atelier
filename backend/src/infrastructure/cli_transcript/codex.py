"""Codex CLI transcript catch-up."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.infrastructure.cli_transcript.common import count_lines, now_iso


def cursor_at_detach(session_id: str, workdir: Path) -> dict[str, Any]:
    path = transcript_path(session_id, workdir)
    return {
        "provider": "codex",
        "line_count": count_lines(path) if path is not None else 0,
    }


def empty_cursor() -> dict[str, Any]:
    return {"provider": "codex", "line_count": 0}


def transcript_path(session_id: str, _workdir: Path) -> Path | None:
    sessions_root = Path.home() / ".codex" / "sessions"
    if not sessions_root.exists():
        return None
    matches = sorted(
        sessions_root.glob(f"**/rollout-*-{session_id}.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def merge(session_id: str, workdir: Path, cursor: dict[str, Any]) -> list[dict[str, Any]]:
    path = transcript_path(session_id, workdir)
    if path is None or not path.exists():
        return []
    start = int(cursor.get("line_count") or 0)
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            if lineno <= start:
                continue
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            events.extend(_translate_entry(entry))
    return events


def _translate_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    if entry.get("type") != "event_msg":
        return []
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return []
    ts = entry.get("timestamp") or now_iso()
    event_type = payload.get("type")
    if event_type == "user_message":
        text = payload.get("message")
        if isinstance(text, str) and text:
            return [{"type": "user_input", "ts": ts, "text": text}]
    if event_type == "agent_message":
        text = payload.get("message")
        if isinstance(text, str) and text:
            return [{"type": "message_complete", "ts": ts, "text": text}]
    if event_type == "task_complete":
        duration_ms = _duration_ms(payload)
        if duration_ms is not None:
            return [
                {
                    "type": "turn_metrics",
                    "ts": ts,
                    "duration_ms": duration_ms,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "last_prompt_tokens": 0,
                    "model": None,
                }
            ]
    return []


def _duration_ms(payload: dict[str, Any]) -> int | None:
    duration = payload.get("duration_ms")
    if isinstance(duration, int) and not isinstance(duration, bool):
        return max(0, duration)
    if isinstance(duration, float):
        return max(0, int(duration))
    started = payload.get("started_at")
    completed = payload.get("completed_at")
    if isinstance(started, int | float) and isinstance(completed, int | float):
        return max(0, int((completed - started) * 1000))
    return None
