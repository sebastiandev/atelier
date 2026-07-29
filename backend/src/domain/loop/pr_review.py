"""Synchronize pull-request review state for one durable loop run."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from src.domain.artifacts.pr_status import (
    PrComment,
    PrLifecycleGateway,
    PrRef,
    fetch_pr_lifecycle,
    parse_pr_url,
    reply_to_pr_comment,
)
from src.domain.loop import actions
from src.domain.loop.models import LoopRunTarget


class PrReviewUnavailable(ValueError):
    """The pull request could not be synchronized with its provider."""


async def refresh(
    target: LoopRunTarget,
    gateway: PrLifecycleGateway,
    *,
    force: bool = False,
) -> None:
    """Refresh remote PR state and post replies for addressed comments.

    Preconditions: the run has a parseable PR URL. Postconditions: remote
    lifecycle fields and comments are current; replies successfully posted for
    addressed comments are marked so later refreshes remain idempotent.
    """
    loop = actions.dict_or_empty(target.run.get("loop"))
    pr = actions.dict_or_empty(loop.get("pr"))
    url = actions.str_or_empty(pr.get("url"))
    ref = parse_pr_url(url)
    if ref is None:
        raise PrReviewUnavailable("run has no supported pull request URL")
    fetched = await fetch_pr_lifecycle(
        gateway,
        ref,
        if_none_match=actions.str_or_none(pr.get("etag")),
        force=force,
    )
    if fetched is None:
        raise PrReviewUnavailable("pull-request status is unavailable")

    comments = _comment_rows(loop.get("pr_comments"))
    if fetched.lifecycle is not None:
        comments = _merge_comments(comments, fetched.lifecycle.comments)
        checks = asdict(fetched.lifecycle.checks)
        pr.update(
            {
                "status": fetched.lifecycle.status,
                "checks": checks,
                "review_state": fetched.lifecycle.review_state,
            }
        )
        if fetched.lifecycle.title:
            pr["title"] = fetched.lifecycle.title
        if fetched.lifecycle.head_branch:
            pr["branch"] = fetched.lifecycle.head_branch
        if fetched.lifecycle.base_branch:
            pr["base"] = fetched.lifecycle.base_branch
        if fetched.lifecycle.head_sha:
            pr["head_sha"] = fetched.lifecycle.head_sha
        if fetched.lifecycle.head_commit_url:
            pr["head_commit_url"] = fetched.lifecycle.head_commit_url
    if fetched.etag:
        pr["etag"] = fetched.etag

    await _post_addressed_replies(gateway, ref, loop, comments)
    pr["last_synced_at"] = actions.now_iso()
    loop["pr"] = pr
    loop["pr_comments"] = comments
    _sync_latest_pr_stage(loop, pr)
    target.run["loop"] = loop


async def post_addressed_replies(
    target: LoopRunTarget,
    gateway: PrLifecycleGateway,
) -> None:
    """Reply to newly addressed comments without fetching remote PR state.

    Preconditions: successful PR completion has marked local comments as
    addressed. Postconditions: successfully posted replies are recorded so
    refresh and later completions remain idempotent.
    """
    loop = actions.dict_or_empty(target.run.get("loop"))
    pr = actions.dict_or_empty(loop.get("pr"))
    ref = parse_pr_url(actions.str_or_empty(pr.get("url")))
    if ref is None:
        return
    comments = _comment_rows(loop.get("pr_comments"))
    await _post_addressed_replies(gateway, ref, loop, comments)
    loop["pr_comments"] = comments
    target.run["loop"] = loop


def _comment_rows(value: object) -> list[dict[str, Any]]:
    return (
        [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    )


def _merge_comments(
    existing: list[dict[str, Any]],
    remote: tuple[PrComment, ...],
) -> list[dict[str, Any]]:
    by_id = {actions.str_or_empty(item.get("id")): item for item in existing}
    merged: list[dict[str, Any]] = []
    for comment in remote:
        prior = by_id.get(comment.id, {})
        merged.append(
            {
                "id": comment.id,
                "author": comment.author,
                "location": comment.location or "",
                "body": comment.body,
                "created_at": comment.created_at,
                "url": comment.url,
                "kind": comment.kind,
                "reply_target_id": comment.reply_target_id,
                "is_viewer": comment.is_viewer,
                "addressed_in_pass": prior.get("addressed_in_pass"),
                "reply_posted_at": prior.get("reply_posted_at"),
            }
        )
    remote_ids = {comment.id for comment in remote}
    merged.extend(
        item
        for item in existing
        if actions.str_or_empty(item.get("id")) not in remote_ids
        and item.get("addressed_in_pass") is not None
    )
    return merged


def _sync_latest_pr_stage(loop: dict[str, Any], pr: dict[str, Any]) -> None:
    """Copy the refreshed lifecycle onto the newest PR stage's snapshot.

    ``capture_completion`` deep-copies the lifecycle onto the stage row when
    the stage passes, and the run view renders that copy in preference to the
    run-level one (a historical occurrence must keep showing the PR as it was
    on *its* pass). Without this the copy freezes at completion, so anything a
    later refresh learns -- check counts, review state, the head commit --
    never reaches the panel the user is looking at.

    The same lifecycle is held in three places: the run, the stage row, and
    the stage's per-pass report. The run view resolves an occurrence's PR as
    ``report.pr ?? stage.pr``, so the report copy is the one actually
    rendered and both have to be refreshed.

    Only the latest PR stage and its newest report are updated; earlier
    passes keep showing the PR as it was on their pass.
    """
    rows = loop.get("stages")
    if not isinstance(rows, list):
        return
    stage = next(
        (
            row
            for row in reversed(rows)
            if isinstance(row, dict) and row.get("kind") == "pr" and row.get("pr")
        ),
        None,
    )
    if stage is None:
        return
    stage["pr"] = deepcopy(pr)
    reports = stage.get("reports")
    if not isinstance(reports, list):
        return
    latest = next(
        (row for row in reversed(reports) if isinstance(row, dict) and row.get("pr")),
        None,
    )
    if latest is not None:
        latest["pr"] = deepcopy(pr)


def _addressed_body(loop: dict[str, Any], pass_number: int, note: str) -> str:
    """Return the reply posted to a comment the loop has addressed.

    Points at the commit that carried the change rather than an Atelier pass
    number: a reviewer can act on a commit link, and "pass 3" means nothing
    outside this tool. Falls back to the pass number when the commit is
    unknown -- an older run, or a PR whose head we could not read.
    """
    pr = actions.dict_or_empty(loop.get("pr"))
    url = actions.str_or_empty(pr.get("head_commit_url"))
    sha = actions.str_or_empty(pr.get("head_sha"))
    if url and sha:
        body = f"Addressed in [`{sha[:7]}`]({url})."
    elif sha:
        body = f"Addressed in `{sha[:7]}`."
    else:
        body = f"Addressed in Atelier pass {pass_number}."
    if note:
        body += f" Applied instruction: {note}"
    return body


async def _post_addressed_replies(
    gateway: PrLifecycleGateway,
    ref: PrRef,
    loop: dict[str, Any],
    comments: list[dict[str, Any]],
) -> None:
    for row in list(comments):
        pass_number = row.get("addressed_in_pass")
        if not isinstance(pass_number, int) or row.get("reply_posted_at"):
            continue
        comment = _domain_comment(row)
        if comment is None:
            continue
        note = _addressed_instruction(loop, comment.id)
        body = _addressed_body(loop, pass_number, note)
        reply = await reply_to_pr_comment(gateway, ref, comment, body)
        if reply is None:
            continue
        posted_at = actions.now_iso()
        row["reply_posted_at"] = posted_at
        comments.append(
            {
                "id": reply.id,
                "author": reply.author,
                "location": reply.location or "",
                "body": reply.body,
                "created_at": reply.created_at,
                "url": reply.url,
                "kind": reply.kind,
                "reply_target_id": reply.reply_target_id,
                "is_viewer": reply.is_viewer,
                "addressed_in_pass": pass_number,
                "reply_posted_at": posted_at,
            }
        )


def _domain_comment(row: dict[str, Any]) -> PrComment | None:
    required = {
        key: actions.str_or_empty(row.get(key))
        for key in ("id", "author", "body", "created_at", "url", "reply_target_id")
    }
    if not required["id"] or not required["created_at"] or not required["reply_target_id"]:
        return None
    kind = row.get("kind")
    if kind not in {"conversation", "review"}:
        return None
    return PrComment(
        id=required["id"],
        author=required["author"],
        location=actions.str_or_none(row.get("location")),
        body=required["body"],
        created_at=required["created_at"],
        url=required["url"],
        kind=kind,
        reply_target_id=required["reply_target_id"],
        is_viewer=row.get("is_viewer") is True,
    )


def _addressed_instruction(loop: dict[str, Any], comment_id: str) -> str:
    stages = loop.get("stages")
    if not isinstance(stages, list):
        return ""
    for stage in reversed(stages):
        if not isinstance(stage, dict):
            continue
        addressed = stage.get("addressed_comments")
        if not isinstance(addressed, list):
            continue
        for item in addressed:
            if isinstance(item, dict) and item.get("comment_id") == comment_id:
                return actions.str_or_empty(item.get("instruction"))
    return ""


__all__ = ["PrReviewUnavailable", "post_addressed_replies", "refresh"]
