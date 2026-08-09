"""One durable record for every request a loop run owes an answer to.

Feedback reached a run through five separate shapes -- an approval's requested
changes, a review gate's send-back, selected pull-request comments, a retry
note, and a stage's corrective note -- each with its own store, its own
lifetime, and its own suppression rule in the orchestrator. The rules
contradicted each other: PR feedback cleared only when the PR stage passed,
while the PR stage could not pass with feedback open, so a run cycled and
pushed on every pass.

The shapes collapse into this record. What differs between them is data:

``scope``
    ``open`` outlives the attempt that carried it; ``attempt`` ends with it.
``answered_by``
    the stage whose pass discharges the request. Clearing is that stage's
    explicit transition, not a side effect of one stage returning one outcome,
    which is what removes the deadlock structurally.

Answered records are never deleted -- they are the run's history of what was
asked and when it was settled.
"""

from __future__ import annotations

from typing import Any

from src.domain.loop import actions

SOURCES = frozenset({"approval", "review", "pr_comment", "pr_general", "retry"})
"""Where a request came from. Rendering and history read this, routing does not."""

OPEN = "open"
ANSWERED = "answered"

SCOPE_OPEN = "open"
"""Outlives the attempt that carried it; only an answer settles it."""
SCOPE_ATTEMPT = "attempt"
"""Ends with the attempt it was sent to, whatever that attempt reports."""

_HEADERS = {
    "approval": "Changes requested by the user, still in force:",
    "review": "Changes requested by the user, still in force:",
    "pr_comment": "Address this pull-request feedback in the current worktree:",
    "pr_general": "Address this pull-request feedback in the current worktree:",
    "retry": "Requested for this attempt:",
}


def record(
    loop: dict[str, Any],
    *,
    source: str,
    pass_number: int,
    note: str = "",
    items: tuple[dict[str, Any], ...] = (),
    answered_by: str = "",
    scope: str = SCOPE_OPEN,
    reason: str = "",
) -> dict[str, Any] | None:
    """Open one feedback record against ``pass_number`` and return it.

    Preconditions: ``loop`` is a mutable loop-run snapshot, ``source`` is one of
    :data:`SOURCES`, and ``pass_number`` is the pass the request applies to,
    already advanced by the caller.
    Postconditions: a request with neither a note nor items stores nothing and
    returns ``None``, because there would be nothing to render. An identical
    open request is not stored twice, so replaying a retry renders it once. The
    record starts ``open`` and stays so until :func:`answer` or
    :func:`answer_all` settles it.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown feedback source: {source}")
    text = note.strip()
    rows = [dict(item) for item in items]
    if not text and not rows:
        return None
    # Scoped to the pass, because that is what the de-duplication is for: a
    # retry replays the request that opened the current pass and it should
    # render once. Across passes an identical request is a *new* one -- the
    # user re-sending a comment because the fix did not land -- and collapsing
    # it into the earlier record loses it, since that record has already been
    # carried by a push and no later push will pick it up again.
    duplicate = next(
        (
            item
            for item, normalised in zip(_stored(loop), records(loop), strict=True)
            if normalised["state"] == OPEN
            and normalised["source"] == source
            and normalised["pass_number"] == pass_number
            and normalised["note"] == text
            and normalised["items"] == rows
        ),
        None,
    )
    if duplicate is not None:
        return duplicate
    entry = {
        "id": _next_id(records(loop)),
        "created_at": actions.now_iso(),
        "pass_number": pass_number,
        "source": source,
        "items": rows,
        "note": text,
        "scope": scope,
        "answered_by": answered_by,
        "state": OPEN,
        "reason": reason,
    }
    stored = loop.get("feedback")
    if not isinstance(stored, list):
        stored = []
        loop["feedback"] = stored
    stored.append(entry)
    return entry


def answer(loop: dict[str, Any], stage_id: str) -> list[dict[str, Any]]:
    """Settle every open record the given stage was to verify, and return them.

    Preconditions: ``loop`` is a mutable loop-run snapshot; ``stage_id`` is the
    stage that just passed. Postconditions: matching records are ``answered``
    and timestamped; nothing is removed, so the run keeps the full account of
    what was asked. Records answered by another stage are untouched.
    """
    if not stage_id:
        return []
    settled = [
        item
        for item, normalised in zip(_stored(loop), records(loop), strict=True)
        if normalised["state"] == OPEN and normalised["answered_by"] == stage_id
    ]
    _settle(settled)
    return settled


def answer_all(loop: dict[str, Any]) -> list[dict[str, Any]]:
    """Settle every open record and return them.

    Accepting a run is the user saying the result is good as it stands, which
    answers whatever was still outstanding regardless of which stage would have
    verified it.

    Preconditions: ``loop`` is a mutable loop-run snapshot.
    Postconditions: no record is left ``open``; none is removed.
    """
    settled = [
        item
        for item, normalised in zip(_stored(loop), records(loop), strict=True)
        if normalised["state"] == OPEN
    ]
    _settle(settled)
    return settled


def close_attempt(loop: dict[str, Any], stage_id: str) -> None:
    """Settle the attempt-scoped records the given stage was retried with.

    Preconditions: ``loop`` is a mutable loop-run snapshot; the stage has just
    reported. Postconditions: its ``attempt``-scoped records are ``answered``,
    because the attempt they belonged to is over whatever the outcome was.
    """
    _settle(
        [
            item
            for item, normalised in zip(_stored(loop), records(loop), strict=True)
            if normalised["state"] == OPEN
            and normalised["scope"] == SCOPE_ATTEMPT
            and normalised["answered_by"] == stage_id
        ]
    )


def context(run: dict[str, Any]) -> str:
    """Return the outstanding requests for a stage prompt, newest first.

    Only open, run-scoped records are instructions. Attempt-scoped ones travel
    with the prompt that launched the attempt and are not repeated here, and
    answered ones are history: rendering either as a live order is what made
    stages act on requests that had already been settled.

    Preconditions: ``run`` is a loop-run snapshot.
    Postconditions: empty when nothing is outstanding; otherwise one block per
    record, skipping any belonging to a pass later than the current one, which
    only happens if a counter was rewound.
    """
    loop = actions.dict_or_empty(run.get("loop"))
    current = max(1, actions.int_or_default(loop.get("pass_number"), 1))
    outstanding = [
        item
        for item in records(loop)
        if item["state"] == OPEN
        and item["scope"] == SCOPE_OPEN
        and item["pass_number"] <= current
    ]
    blocks = [render(item) for item in reversed(outstanding)]
    return "\n\n".join(block for block in blocks if block)


def render(entry: dict[str, Any]) -> str:
    """Render one record as a prompt block under its source's heading.

    Reads every field defensively: ``record`` hands back the stored dict when a
    request is a duplicate, and a stored dict is only guaranteed to carry a
    readable ``source``.
    """
    lines = [_HEADERS.get(actions.str_or_empty(entry.get("source")), _HEADERS["approval"])]
    rows = entry.get("items")
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    for item in rows:
        author = actions.str_or_empty(item.get("author")) or "reviewer"
        location = actions.str_or_empty(item.get("location"))
        lines.append(
            f"- {author}{f' at {location}' if location else ''}: "
            f"{actions.str_or_empty(item.get('body'))}"
        )
        thread = item.get("thread")
        if isinstance(thread, list) and len(thread) > 1:
            lines.append("  Thread context:")
            for reply in thread[1:]:
                if not isinstance(reply, dict):
                    continue
                reply_author = actions.str_or_empty(reply.get("author")) or "reviewer"
                lines.append(f"  - {reply_author}: {actions.str_or_empty(reply.get('body'))}")
        instruction = actions.str_or_empty(item.get("instruction"))
        if instruction:
            lines.append(f"  User instruction: {instruction}")
    note = actions.str_or_empty(entry.get("note"))
    if note:
        # A note on its own reads as a sentence; alongside items it is one more
        # entry in the list, so the stage can tell where each request ends.
        lines.append(f"- User instruction: {note}" if rows else note)
    return "\n".join(lines) if len(lines) > 1 else ""


def records(loop: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a normalised copy of every readable record, oldest first."""
    rows = []
    for item in _stored(loop):
        source = actions.str_or_empty(item.get("source"))
        rows.append(
            {
                # Unknown keys ride along: a source may need bookkeeping of its
                # own -- which push carried a PR request, say -- and that
                # belongs on the record rather than in a parallel store.
                **item,
                "id": actions.str_or_empty(item.get("id")),
                "created_at": actions.str_or_empty(item.get("created_at")),
                "pass_number": actions.int_or_default(item.get("pass_number"), 0),
                "source": source,
                "items": [row for row in item.get("items", []) if isinstance(row, dict)]
                if isinstance(item.get("items"), list)
                else [],
                "note": actions.str_or_empty(item.get("note")).strip(),
                "scope": actions.str_or_empty(item.get("scope")) or SCOPE_OPEN,
                "answered_by": actions.str_or_empty(item.get("answered_by")),
                "state": ANSWERED
                if actions.str_or_empty(item.get("state")) == ANSWERED
                else OPEN,
                "reason": actions.str_or_empty(item.get("reason")),
                "answered_at": actions.str_or_empty(item.get("answered_at")),
            }
        )
    return rows


def from_sources(loop: dict[str, Any], sources: frozenset[str]) -> list[dict[str, Any]]:
    """Return every record from the given sources, oldest first.

    Open or answered: what a request asked for is a fact about the run, and a
    source that needs its own subset -- a push reporting what it carried, say
    -- filters this rather than reading the open set. Answering and being
    acted on are different events, and only the first is this store's rule.
    """
    return [item for item in records(loop) if item["source"] in sources]


def _stored(loop: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the readable records as the snapshot holds them, oldest first.

    The entries themselves are the stored dicts, so settling one settles it on
    the snapshot. Reading never creates the key: a run that has no feedback has
    to round-trip through reconcile byte for byte, and a query that writes an
    empty list would change every such snapshot the first time a prompt is
    built. Malformed entries are dropped rather than raised on: the snapshot is
    data the run has to keep moving through, and a request nobody can read is
    not worth failing a stage over.
    """
    value = loop.get("feedback")
    return [
        item
        for item in (value if isinstance(value, list) else [])
        if isinstance(item, dict) and actions.str_or_empty(item.get("source")) in SOURCES
    ]


def update(loop: dict[str, Any], entry_id: str, **fields: Any) -> None:
    """Merge source-specific bookkeeping into one stored record.

    Preconditions: ``loop`` is a mutable loop-run snapshot and ``entry_id``
    names a stored record. Postconditions: the record carries the given fields;
    an unknown id changes nothing, because a snapshot missing a record is data
    the run still has to move through.
    """
    for item in _stored(loop):
        if item.get("id") == entry_id:
            item.update(fields)
            return


def waive(loop: dict[str, Any], findings: list[str]) -> None:
    """Record findings the user chose not to act on, run-wide.

    Preconditions: ``loop`` is a mutable loop-run snapshot.
    Postconditions: each finding appears once, in the order it was first
    dismissed, so a later reviewer can be told not to raise it again.
    """
    stored = actions.str_list(loop.get("waived_findings"))
    for finding in findings:
        text = finding.strip()
        if text and text not in stored:
            stored.append(text)
    loop["waived_findings"] = stored


def dismissed(loop: dict[str, Any]) -> list[str]:
    """Return the findings the user has already dismissed on this run."""
    return actions.str_list(loop.get("waived_findings"))


def _settle(entries: list[dict[str, Any]]) -> None:
    now = actions.now_iso()
    for entry in entries:
        entry["state"] = ANSWERED
        entry["answered_at"] = now


def _next_id(stored: list[dict[str, Any]]) -> str:
    used = {
        int(item["id"][3:])
        for item in stored
        if item["id"].startswith("fb-") and item["id"][3:].isdigit()
    }
    return f"fb-{max(used, default=0) + 1}"


__all__ = [
    "ANSWERED",
    "OPEN",
    "SCOPE_ATTEMPT",
    "SCOPE_OPEN",
    "SOURCES",
    "answer",
    "answer_all",
    "close_attempt",
    "context",
    "dismissed",
    "from_sources",
    "record",
    "records",
    "render",
    "update",
    "waive",
]
