"""Story-PR tracking state kept on a PR artifact row.

The ``lifecycle`` dict on ``PrArtifact`` holds:

- ``opener_spec`` -- a snapshot of the agent that opened the PR, taken when
  the artifact was recorded. ``artifacts.agent_id`` is ``ON DELETE SET
  NULL``, so this is what survives the opener being deleted and lets the
  story view relaunch an agent with the same specs.
- ``pr`` -- the last fetched lifecycle (status, checks, branches, head).
- ``comments`` -- merged comment rows, same shape as a loop run's
  ``pr_comments`` so the frontend thread grouping is shared.
- ``feedback`` -- every batch sent back to the opener: which comments,
  the mode (``implement`` / ``discuss``), the head at send time, and when
  a reply was posted for it.

Everything here is pure; persistence and the gateway calls live in the
command.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from src.domain.artifacts.pr_status import PrLifecycle, PrRef
from src.domain.models import Agent

FeedbackMode = Literal["implement", "discuss"]


def opener_spec(agent: Agent) -> dict[str, Any]:
    """Snapshot the launch-relevant fields of the opening agent.

    Preconditions: ``agent`` is persisted (has a slug).
    Postconditions: the result is JSON-serialisable and enough to rebuild an
    equivalent ``AgentLaunchRequest`` (branch comes from the PR itself).
    """
    return {
        "slug": agent.slug,
        "name": agent.name,
        "persona": agent.persona,
        "role": agent.role,
        "provider": agent.provider,
        "model": agent.model,
        "options": dict(agent.options) if agent.options else {},
        "folder": str(agent.folder),
        "artifact_id": agent.artifact_id,
    }


def apply_fetch(
    lifecycle: dict[str, Any],
    fetched: PrLifecycle,
    *,
    url: str,
    ref: PrRef,
    etag: str | None,
    merge_comments: Any,
) -> dict[str, Any]:
    """Fold a fetched lifecycle into the stored state.

    Preconditions: ``fetched`` came from the PR at ``url``.
    Postconditions: ``pr`` mirrors the remote snapshot; comment rows keep
    their local marks; ``last_synced_at`` is now.
    """
    pr = dict(lifecycle.get("pr") or {})
    pr.update(
        {
            "url": url,
            "number": ref.number,
            "status": fetched.status,
            "checks": asdict(fetched.checks),
            "review_state": fetched.review_state,
            "title": fetched.title or pr.get("title", ""),
            "branch": fetched.head_branch or pr.get("branch", ""),
            "base": fetched.base_branch or pr.get("base", ""),
            "head_sha": fetched.head_sha or pr.get("head_sha", ""),
            "head_commit_url": fetched.head_commit_url or pr.get("head_commit_url", ""),
            "body": fetched.body,
            "additions": fetched.additions,
            "deletions": fetched.deletions,
            "changed_files": fetched.changed_files,
            "last_synced_at": now_iso(),
        }
    )
    if etag:
        pr["etag"] = etag
    existing = [dict(row) for row in lifecycle.get("comments") or [] if isinstance(row, dict)]
    return {
        **lifecycle,
        "pr": pr,
        "comments": merge_comments(existing, fetched.comments),
    }


def touch_synced(lifecycle: dict[str, Any], *, url: str, ref: PrRef) -> dict[str, Any]:
    """Record a 304 (nothing changed) so the view still shows a sync time."""
    pr = dict(lifecycle.get("pr") or {})
    pr.setdefault("url", url)
    pr.setdefault("number", ref.number)
    pr["last_synced_at"] = now_iso()
    return {**lifecycle, "pr": pr}


def record_feedback(
    lifecycle: dict[str, Any],
    *,
    comment_ids: list[str],
    instructions: dict[str, str],
    note: str,
    mode: FeedbackMode,
    sent_to: str,
) -> dict[str, Any]:
    """Append one feedback batch and mark its comments as sent.

    Preconditions: ``comment_ids`` exist in ``lifecycle["comments"]``.
    Postconditions: each selected row carries ``feedback_mode`` and
    ``feedback_sent_at``; a ``discuss`` batch is closed immediately (nothing
    to wait for), an ``implement`` batch waits for a new head.
    """
    now = now_iso()
    head = str((lifecycle.get("pr") or {}).get("head_sha") or "")
    rows = [dict(row) for row in lifecycle.get("comments") or [] if isinstance(row, dict)]
    wanted = set(comment_ids)
    for row in rows:
        if row.get("id") in wanted:
            row["feedback_mode"] = mode
            row["feedback_sent_at"] = now
    batch = {
        "sent_at": now,
        "sent_to": sent_to,
        "mode": mode,
        "note": note,
        "head_sha_at_send": head,
        "replied_at": now if mode == "discuss" else None,
        "comments": [
            {"comment_id": cid, "instruction": instructions.get(cid, "")} for cid in comment_ids
        ],
    }
    feedback = [dict(item) for item in lifecycle.get("feedback") or [] if isinstance(item, dict)]
    feedback.append(batch)
    return {**lifecycle, "comments": rows, "feedback": feedback}


def pending_replies(lifecycle: dict[str, Any]) -> list[tuple[dict[str, Any], str, str]]:
    """Comments whose implement batch has been pushed but not yet answered.

    Returns ``(batch, comment_id, instruction)`` triples for every implement
    batch whose ``head_sha_at_send`` differs from the current head. A batch
    sent before the head was known never qualifies -- there is nothing to
    cite -- until a later fetch records a head.
    """
    head = str((lifecycle.get("pr") or {}).get("head_sha") or "")
    if not head:
        return []
    out: list[tuple[dict[str, Any], str, str]] = []
    for batch in lifecycle.get("feedback") or []:
        if not isinstance(batch, dict) or batch.get("mode") != "implement":
            continue
        if batch.get("replied_at") or batch.get("head_sha_at_send") == head:
            continue
        for item in batch.get("comments") or []:
            if isinstance(item, dict) and item.get("comment_id"):
                out.append((batch, str(item["comment_id"]), str(item.get("instruction") or "")))
    return out


def addressed_body(lifecycle: dict[str, Any], instruction: str) -> str:
    """The one-line reply posted to a comment once the push landed."""
    pr = lifecycle.get("pr") or {}
    sha = str(pr.get("head_sha") or "")
    url = str(pr.get("head_commit_url") or "")
    citation = f"[`{sha[:7]}`]({url})" if url and sha else f"`{sha[:7]}`"
    body = f"Addressed in {citation}."
    if instruction.strip():
        body += f" Applied instruction: {instruction.strip()}"
    return body


def feedback_prompt(
    lifecycle: dict[str, Any],
    *,
    comment_ids: list[str],
    instructions: dict[str, str],
    note: str,
    mode: FeedbackMode,
) -> str:
    """Render the message sent to the opening agent for a feedback batch."""
    pr = lifecycle.get("pr") or {}
    rows = {
        str(row.get("id")): row
        for row in lifecycle.get("comments") or []
        if isinstance(row, dict)
    }
    lines = [f"Pull request feedback on {pr.get('url', '')}"]
    if pr.get("branch"):
        lines.append(f"Branch: {pr['branch']} → {pr.get('base', '')}")
    if mode == "implement":
        lines.append(
            "Address each comment below on this PR's branch, commit, and push to the "
            "same pull request. Do not open another PR. When done, report the commit."
        )
    else:
        lines.append(
            "Answer each comment below here in the chat. Do not edit files or push; "
            "this is a discussion, not a change request."
        )
    for cid in comment_ids:
        row = rows.get(cid, {})
        where = row.get("location") or "general"
        lines.append(f"- [{cid}] @{row.get('author', '?')} ({where}): {row.get('body', '')}")
        instruction = instructions.get(cid, "").strip()
        if instruction:
            lines.append(f"  User instruction: {instruction}")
    if note.strip():
        lines.append(f"Note for the whole batch: {note.strip()}")
    return "\n".join(lines)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "FeedbackMode",
    "addressed_body",
    "apply_fetch",
    "feedback_prompt",
    "opener_spec",
    "pending_replies",
    "record_feedback",
    "touch_synced",
]
