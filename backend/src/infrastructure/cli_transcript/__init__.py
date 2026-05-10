"""Catch-up merge from CLI transcripts back into Atelier's NDJSON ledger.

When the user detaches an agent to CLI, types a few prompts there, then
re-attaches in Atelier, this module pulls the new entries from the
provider's source-of-truth and translates them into AgentEvent-shaped
dicts so the user's transcript view stays whole.

  - Claude Code: ``~/.claude/projects/<munged-cwd>/<session_id>.jsonl``
    where the cwd has its slashes replaced with dashes.
  - Amp: shells out to ``amp threads export <id>`` (Amp threads are
    server-side, not on disk in modern versions). Output is the same
    JSON shape as Amp used to write locally.

Cursor strategy: at detach time the route writes a ``user_detached``
event to our NDJSON whose payload includes an ``sdk_cursor`` snapshot
(timestamp for Claude, message count for Amp). On re-attach the merge
finds that event, reads the SDK source, emits everything past the cursor.

Translation is best-effort. Both providers use Anthropic-shaped content
blocks (``text`` / ``thinking`` / ``tool_use`` / ``tool_result``) so the
mapping into Atelier's event types is largely shared.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.domain.models import Provider

_log = logging.getLogger(__name__)
_AMP_EXPORT_TIMEOUT_S = 30.0


def sdk_cursor_at_detach(provider: Provider, session_id: str, workdir: Path) -> dict[str, Any]:
    """Snapshot the SDK source's current state so a later merge knows
    where to start. Returned dict goes onto the ``user_detached``
    transcript event as ``sdk_cursor``."""
    if provider == "claude-code":
        # Claude is append-only jsonl; timestamps in the file are reliable
        # so "now" is the anchor and the merge filters by it.
        return {"provider": "claude-code", "anchor_ts": _now_iso()}
    if provider == "amp":
        thread = _fetch_amp_thread(session_id)
        count = len((thread or {}).get("messages") or [])
        return {"provider": "amp", "message_count": count}
    return {}


def merge_cli_transcript(
    provider: Provider,
    session_id: str,
    workdir: Path,
    cursor: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Translate everything past ``cursor`` from the SDK source into
    AgentEvent-shaped dicts. Caller stamps seqs and appends to NDJSON.

    ``cursor`` is the dict produced by ``sdk_cursor_at_detach``; pass
    ``None`` to start from the source's beginning (treats everything as
    new — only useful in tests or recovery scenarios).
    """
    cursor = cursor or _empty_cursor(provider)

    if provider == "claude-code":
        path = sdk_transcript_path(provider, session_id, workdir)
        if path is None or not path.exists():
            return []
        return _merge_claude(path, cursor)
    if provider == "amp":
        thread = _fetch_amp_thread(session_id)
        if thread is None:
            return []
        return _merge_amp(thread, cursor)
    return []


# ---------------------------------------------------------------------------
# Amp source fetcher
#
# Modern Amp threads are server-side; ``amp threads export <id>`` is the
# stable interface that prints the full thread JSON to stdout. We shell
# out instead of reading a local file (older versions wrote to
# ``~/.local/share/amp/threads/`` but those files are stale on current
# installs). Failure modes — no ``amp`` on PATH, network error, auth
# expired, unknown thread — all return ``None`` so the caller treats the
# merge as a no-op rather than failing the re-attach.


def _fetch_amp_thread(session_id: str) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            ["amp", "threads", "export", session_id],
            capture_output=True,
            timeout=_AMP_EXPORT_TIMEOUT_S,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("amp threads export failed for %s: %s", session_id, exc)
        return None
    if result.returncode != 0:
        _log.warning(
            "amp threads export returned %s for %s: %s",
            result.returncode,
            session_id,
            (result.stderr or "").strip(),
        )
        return None
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        _log.warning("amp threads export gave non-JSON for %s: %s", session_id, exc)
        return None


# ---------------------------------------------------------------------------
# Path resolution


def sdk_transcript_path(
    provider: Provider, session_id: str, workdir: Path
) -> Path | None:
    """The on-disk file Atelier reads for the catch-up merge.

    Only meaningful for Claude (which writes append-only jsonl). Amp
    threads are server-side and accessed via ``amp threads export``;
    this function returns None for Amp so callers can branch cleanly.
    """
    if provider == "claude-code":
        # Claude derives its project dir name by replacing every ``/``
        # in the cwd with ``-``. Leading slash → leading dash, so
        # ``/Users/seba/src/atelier`` → ``-Users-seba-src-atelier``.
        project = str(workdir).replace("/", "-")
        return Path.home() / ".claude" / "projects" / project / f"{session_id}.jsonl"
    return None


# ---------------------------------------------------------------------------
# Claude jsonl reader


def _merge_claude(path: Path, cursor: dict[str, Any]) -> list[dict[str, Any]]:
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
            events.extend(_translate_claude_entry(entry, ts))
    return events


def _translate_claude_entry(entry: dict[str, Any], ts: str) -> list[dict[str, Any]]:
    entry_type = entry.get("type")
    if entry_type not in ("user", "assistant"):
        # housekeeping: permission-mode, file-history-snapshot, attachment
        return []
    message = entry.get("message") or {}
    return _translate_anthropic_message(message, ts)


# ---------------------------------------------------------------------------
# Amp thread reader (operates on the dict returned by ``amp threads export``)


def _merge_amp(thread: dict[str, Any], cursor: dict[str, Any]) -> list[dict[str, Any]]:
    messages = thread.get("messages") or []
    start = int(cursor.get("message_count") or 0)
    events: list[dict[str, Any]] = []
    for msg in messages[start:]:
        if not isinstance(msg, dict):
            continue
        ts = _amp_message_ts(msg)
        events.extend(_translate_anthropic_message(msg, ts))
    return events


def _amp_message_ts(msg: dict[str, Any]) -> str:
    sent_at = (msg.get("meta") or {}).get("sentAt")
    if isinstance(sent_at, int | float):
        # Amp stores epoch ms.
        return datetime.fromtimestamp(sent_at / 1000.0, UTC).isoformat()
    return _now_iso()


# ---------------------------------------------------------------------------
# Shared Anthropic-content-block translator


def _translate_anthropic_message(
    message: dict[str, Any], ts: str
) -> list[dict[str, Any]]:
    role = message.get("role")
    content = message.get("content")
    if role == "user":
        return _translate_user_content(content, ts)
    if role == "assistant":
        events = _translate_assistant_content(content, ts)
        # CLI-side turns don't reach our adapters, so the live
        # ``turn_metrics`` emit pathway is bypassed. Pull ``usage`` off
        # the assistant message ourselves so the FE's session-cost +
        # ctx% counters cover the detached period too. ``duration_ms``
        # is 0 because the merged source doesn't carry wall-clock; only
        # token usage is reconstructable from the export.
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
    input_tokens = _usage_int(usage, "input_tokens")
    cache_read = _usage_int(usage, "cache_read_input_tokens")
    cache_create = _usage_int(usage, "cache_creation_input_tokens")
    return {
        "type": "turn_metrics",
        "ts": ts,
        "duration_ms": 0,
        "input_tokens": input_tokens,
        "output_tokens": _usage_int(usage, "output_tokens"),
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
        # Each merged message is one API call (CLI exports are per-call,
        # not aggregated like ResultMessage), so the prompt size for ctx%
        # is exactly this call's input + cache lookups.
        "last_prompt_tokens": input_tokens + cache_read + cache_create,
        "model": message.get("model"),
    }


def _usage_int(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _translate_user_content(content: Any, ts: str) -> list[dict[str, Any]]:
    # Anthropic allows two shapes for user messages:
    #   - bare string (CLI prompt: "fix the bug")
    #   - list of blocks, where blocks may be ``text`` or ``tool_result``
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
            # Two shapes: Anthropic (`tool_use_id` + `content`) and Amp
            # (`toolUseID` + `run.result.content`, plus `run.status` for
            # the error flag). We accept both so the same translator
            # handles either provider's export.
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
    """Anthropic tool_result's ``content`` can be a string OR an array
    of content blocks. Flatten to a single string for our event dict —
    we don't carry nested block structure in ``ToolResult``."""
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


# ---------------------------------------------------------------------------


def _empty_cursor(provider: Provider) -> dict[str, Any]:
    if provider == "claude-code":
        return {"provider": "claude-code", "anchor_ts": _now_iso()}
    if provider == "amp":
        return {"provider": "amp", "message_count": 0}
    return {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "merge_cli_transcript",
    "sdk_cursor_at_detach",
    "sdk_transcript_path",
]
