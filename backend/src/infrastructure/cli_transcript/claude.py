"""Claude Code CLI transcript catch-up."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.infrastructure.cli_transcript.common import now_iso


def cursor_at_detach(_session_id: str, _workdir: Path) -> dict[str, Any]:
    return {"provider": "claude-code", "anchor_ts": now_iso()}


def empty_cursor() -> dict[str, Any]:
    return {"provider": "claude-code", "anchor_ts": now_iso()}


def transcript_path(session_id: str, workdir: Path) -> Path:
    project = str(workdir).replace("/", "-")
    return Path.home() / ".claude" / "projects" / project / f"{session_id}.jsonl"


def merge(session_id: str, workdir: Path, cursor: dict[str, Any]) -> list[dict[str, Any]]:
    path = transcript_path(session_id, workdir)
    if not path.exists():
        return []
    anchor = cursor.get("anchor_ts") or ""
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            ts = entry.get("timestamp") or ""
            if anchor and ts <= anchor:
                continue
            events.extend(_translate_entry(entry, ts))
    return events


def _translate_entry(entry: dict[str, Any], ts: str) -> list[dict[str, Any]]:
    entry_type = entry.get("type")
    if entry_type not in ("user", "assistant"):
        return []
    message = entry.get("message") or {}
    return translate_anthropic_message(message, ts)


def translate_anthropic_message(
    message: dict[str, Any], ts: str
) -> list[dict[str, Any]]:
    role = message.get("role")
    content = message.get("content")
    if role == "user":
        return _translate_user_content(content, ts)
    if role == "assistant":
        events = _translate_assistant_content(content, ts)
        rollup = _assistant_turn_metrics(message, ts)
        if rollup is not None:
            events.append(rollup)
        return events
    return []


def _assistant_turn_metrics(
    message: dict[str, Any], ts: str
) -> dict[str, Any] | None:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = _usage_int(usage, "input_tokens", "inputTokens")
    cache_read = _usage_int(usage, "cache_read_input_tokens", "cacheReadInputTokens")
    cache_create = _usage_int(
        usage, "cache_creation_input_tokens", "cacheCreationInputTokens"
    )
    return {
        "type": "turn_metrics",
        "ts": ts,
        "duration_ms": 0,
        "input_tokens": input_tokens,
        "output_tokens": _usage_int(usage, "output_tokens", "outputTokens"),
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
        "last_prompt_tokens": input_tokens + cache_read + cache_create,
        "model": message.get("model") or usage.get("model"),
    }


def _usage_int(usage: dict[str, Any], *keys: str) -> int:
    value = next((usage[key] for key in keys if key in usage), 0)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _translate_user_content(content: Any, ts: str) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "user_input", "ts": ts, "text": content}]
    if not isinstance(content, list):
        return []
    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text") or "")
        elif block_type == "tool_result":
            run = block.get("run")
            if isinstance(run, dict):
                tool_id = block.get("toolUseID") or block.get("tool_use_id") or ""
                result_payload = run.get("result")
                if isinstance(result_payload, dict):
                    content_val: Any = result_payload.get("content")
                else:
                    content_val = result_payload
                is_error = run.get("status") == "error"
            else:
                tool_id = block.get("tool_use_id") or block.get("toolUseID") or ""
                content_val = block.get("content")
                is_error = bool(block.get("is_error"))
            events.append(
                {
                    "type": "tool_result",
                    "ts": ts,
                    "tool_id": tool_id,
                    "content": _flatten_tool_result_content(content_val),
                    "is_error": is_error,
                }
            )
    if text_parts:
        events.insert(
            0,
            {
                "type": "user_input",
                "ts": ts,
                "text": "\n".join(p for p in text_parts if p),
            },
        )
    return events


def _translate_assistant_content(content: Any, ts: str) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    events: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text") or ""
            if text:
                events.append({"type": "message_complete", "ts": ts, "text": text})
        elif block_type == "thinking":
            text = block.get("thinking") or ""
            if text:
                events.append({"type": "thinking_complete", "ts": ts, "text": text})
        elif block_type == "tool_use":
            events.append(
                {
                    "type": "tool_call",
                    "ts": ts,
                    "tool_id": block.get("id") or "",
                    "name": block.get("name") or "",
                    "arguments": block.get("input") or {},
                }
            )
    return events


def _flatten_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text") or "")
            else:
                parts.append(json.dumps(block))
        return "\n".join(parts)
    return ""
