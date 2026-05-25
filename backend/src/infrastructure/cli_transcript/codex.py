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
    last_token_count: dict[str, Any] | None = None
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
            payload = entry.get("payload") if entry.get("type") == "event_msg" else None
            if isinstance(payload, dict) and payload.get("type") == "token_count":
                last_token_count = payload
                continue
            events.extend(_translate_entry(entry, last_token_count))
            if isinstance(payload, dict) and payload.get("type") == "task_complete":
                last_token_count = None
    return events


def _translate_entry(
    entry: dict[str, Any],
    token_count: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
        duration_ms = _duration_ms(payload) or 0
        metrics = _metrics_from_token_count(token_count)
        if duration_ms or metrics is not None:
            metrics = metrics or _empty_metrics()
            return [
                {
                    "type": "turn_metrics",
                    "ts": ts,
                    "duration_ms": duration_ms,
                    **metrics,
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


def _metrics_from_token_count(payload: dict[str, Any] | None) -> dict[str, int] | None:
    if payload is None:
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    total_usage = info.get("total_token_usage")
    if not isinstance(total_usage, dict):
        return None
    last_usage = info.get("last_token_usage")
    input_tokens, cache_read_tokens = _split_prompt_usage(total_usage)
    metrics = {
        "input_tokens": input_tokens,
        "output_tokens": _non_negative_int(total_usage.get("output_tokens")),
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": 0,
        "last_prompt_tokens": _prompt_tokens_from_usage(last_usage)
        if isinstance(last_usage, dict)
        else 0,
    }
    context_window = _positive_int(info.get("model_context_window"))
    if context_window is not None:
        metrics["context_window"] = context_window
    return metrics


def _empty_metrics() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "last_prompt_tokens": 0,
    }


def _prompt_tokens_from_usage(usage: dict[str, Any]) -> int:
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    if "cached_input_tokens" in usage:
        return input_tokens
    return (
        input_tokens
        + _non_negative_int(usage.get("cache_read_input_tokens"))
        + _non_negative_int(usage.get("cache_creation_input_tokens"))
    )


def _split_prompt_usage(usage: dict[str, Any]) -> tuple[int, int]:
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    cached_tokens = _non_negative_int(usage.get("cached_input_tokens"))
    if cached_tokens:
        return max(0, input_tokens - cached_tokens), cached_tokens
    return input_tokens, _non_negative_int(usage.get("cache_read_input_tokens"))


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _positive_int(value: Any) -> int | None:
    parsed = _non_negative_int(value)
    return parsed if parsed > 0 else None
