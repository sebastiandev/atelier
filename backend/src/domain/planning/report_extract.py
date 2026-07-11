"""Structured report extraction for planning artifact runs."""

from __future__ import annotations

import json
import re
from typing import Any

_REPORT_RE = re.compile(
    r'^\s*(\{\s*"atelier_loop_report"\s*:.*\})\s*$',
    re.MULTILINE,
)
_REPORT_FIELDS = (
    "summary",
    "divergences",
    "skipped_scope",
    "blockers",
    "decisions",
    "changes",
    "validation_evidence",
)
_EMPTY_REPORT = {
    "summary": "",
    "divergences": "",
    "skipped_scope": "",
    "blockers": "",
    "decisions": "",
    "changes": "",
    "validation_evidence": "",
    "proposed_source": "",
}


def extract_latest_report(events: list[dict[str, Any]]) -> tuple[dict[str, str], int] | None:
    """Extract the latest assistant report from transcript events.

    Preconditions: ``events`` are transcript rows ordered by sequence.
    Postconditions: returns report fields plus source sequence, or ``None``.
    """
    candidates: list[tuple[str, int]] = []
    for event in events:
        if event.get("type") != "message_complete":
            continue
        text = event.get("text")
        seq = event.get("seq")
        if isinstance(text, str) and text.strip() and isinstance(seq, int):
            candidates.append((text, seq))
    if not candidates:
        return None
    text, seq = candidates[-1]
    return _json_report(text), seq


def _json_report(text: str) -> dict[str, str]:
    match = _REPORT_RE.search(text)
    if match is None:
        return dict(_EMPTY_REPORT)
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return dict(_EMPTY_REPORT)
    body = parsed.get("atelier_loop_report")
    if not isinstance(body, dict):
        return dict(_EMPTY_REPORT)
    report = dict(_EMPTY_REPORT)
    for field in _REPORT_FIELDS:
        report[field] = _string_field(body.get(field))
    report["proposed_source"] = _string_field(body.get("proposed_source"))
    return report


def _string_field(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = ["extract_latest_report"]
