"""Amp CLI transcript catch-up."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.infrastructure.cli_transcript.claude import translate_anthropic_message
from src.infrastructure.cli_transcript.common import now_iso

_log = logging.getLogger(__name__)
_EXPORT_TIMEOUT_S = 30.0


def cursor_at_detach(session_id: str, _workdir: Path) -> dict[str, Any]:
    thread = fetch_thread(session_id)
    count = len((thread or {}).get("messages") or [])
    return {"provider": "amp", "message_count": count}


def empty_cursor() -> dict[str, Any]:
    return {"provider": "amp", "message_count": 0}


def transcript_path(_session_id: str, _workdir: Path) -> None:
    return None


def merge(session_id: str, _workdir: Path, cursor: dict[str, Any]) -> list[dict[str, Any]]:
    thread = fetch_thread(session_id)
    if thread is None:
        return []
    messages = thread.get("messages") or []
    start = int(cursor.get("message_count") or 0)
    events: list[dict[str, Any]] = []
    for msg in messages[start:]:
        if not isinstance(msg, dict):
            continue
        events.extend(translate_anthropic_message(msg, _message_ts(msg)))
    return events


def fetch_thread(session_id: str) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            ["amp", "threads", "export", session_id],
            capture_output=True,
            timeout=_EXPORT_TIMEOUT_S,
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


def _message_ts(msg: dict[str, Any]) -> str:
    sent_at = (msg.get("meta") or {}).get("sentAt")
    if isinstance(sent_at, int | float):
        return datetime.fromtimestamp(sent_at / 1000.0, UTC).isoformat()
    return now_iso()
