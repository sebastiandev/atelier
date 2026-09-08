"""Synchronize pull-request review state for one durable loop run."""

from __future__ import annotations

import re
from collections.abc import Callable
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
    save: Callable[[], None] | None = None,
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

    def checkpoint() -> None:
        loop["pr"] = pr
        loop["pr_comments"] = comments
        target.run["loop"] = loop
        if save is not None:
            save()

    await _post_addressed_replies(gateway, ref, loop, comments, checkpoint)
    pr["last_synced_at"] = actions.now_iso()
    loop["pr"] = pr
    loop["pr_comments"] = comments
    target.run["loop"] = loop


async def post_addressed_replies(
    target: LoopRunTarget,
    gateway: PrLifecycleGateway,
    save: Callable[[], None] | None = None,
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

    def checkpoint() -> None:
        loop["pr_comments"] = comments
        target.run["loop"] = loop
        if save is not None:
            save()

    await _post_addressed_replies(gateway, ref, loop, comments, checkpoint)
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
                "dismissed_at": prior.get("dismissed_at"),
                "addressed_note": prior.get("addressed_note"),
                "atelier_reply": prior.get("atelier_reply"),
            }
        )
    remote_ids = {comment.id for comment in remote}
    # A row the remote no longer returns is kept only when the run said
    # something about it -- sent it through a pass, or dismissed it. Dropping
    # a dismissed row would resurrect it as open on the next refresh.
    merged.extend(
        item
        for item in existing
        if actions.str_or_empty(item.get("id")) not in remote_ids
        and (item.get("addressed_in_pass") is not None or item.get("dismissed_at"))
    )
    return merged


def _addressed_body(
    loop: dict[str, Any],
    pass_number: int,
    note: str,
    addressed_note: str = "",
) -> str:
    """Return the reply posted to a comment the loop has addressed.

    Points at the commit that carried the change rather than an Atelier pass
    number: a reviewer can act on a commit link, and "pass 3" means nothing
    outside this tool. Falls back to the pass number when the commit is
    unknown -- an older run, or a PR whose head we could not read.

    One or two lines. A reviewer reading a thread wants to know it was handled
    and where to look, not to read the run's report; anything longer buries the
    conversation it is replying to.
    """
    pr = actions.dict_or_empty(loop.get("pr"))
    url = actions.str_or_empty(pr.get("head_commit_url"))
    sha = actions.str_or_empty(pr.get("head_sha"))
    if url and sha:
        citation = f"[`{sha[:7]}`]({url})"
    elif sha:
        citation = f"`{sha[:7]}`"
    else:
        citation = f"Atelier pass {pass_number}"
    # What the push said it did about *this* comment. The pass-level report
    # used to fill this slot, which put one identical paragraph on every
    # thread the push answered -- reviewers read it as a form letter and said
    # so on the pull request. No per-comment answer now means no claim.
    detail = _one_or_two_lines(addressed_note)
    if detail:
        # The stage's own sentence leads and carries whatever claim is true --
        # it is asked to say so plainly when a comment was considered and
        # deliberately not acted on, and "Addressed in <sha>. Left as is."
        # contradicts itself on a public thread. The commit is a pointer
        # after it, not an assertion about this comment.
        return f"{detail} ({citation})"
    body = f"Addressed in {citation}."
    if note:
        # The user's instruction, kept labelled: it says what was asked for,
        # not what was done, and unlabelled it would read as the latter.
        body += f" Applied instruction: {note}"
    return body


_DETAIL_LIMIT = 240


def _one_or_two_lines(text: str) -> str:
    """Collapse a report field to a short, safely quotable line.

    No sentence splitting: it cut on the first ``". "``, so "Fixed the i.e.
    case. Then hardened the parser." was posted as "Fixed the i.e." -- a
    truncated non-sentence presented as the account of what changed. The cap
    lands on a word boundary instead.

    ``@`` and ``#`` are escaped. This text is an agent's prose going onto a
    public pull request, where ``@handle`` notifies a real person and ``#123``
    cross-links an issue; neither is something a report meant to do, and both
    would happen on every addressed comment.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    if len(collapsed) > _DETAIL_LIMIT:
        cut = collapsed[:_DETAIL_LIMIT]
        boundary = cut.rfind(" ")
        collapsed = (cut[:boundary] if boundary > 0 else cut).rstrip(" ,;:") + "\u2026"
    return _MENTION.sub(r"\\\g<0>", collapsed)


_MENTION = re.compile(r"[@#](?=\w)")


async def _post_addressed_replies(
    gateway: PrLifecycleGateway,
    ref: PrRef,
    loop: dict[str, Any],
    comments: list[dict[str, Any]],
    checkpoint: Callable[[], None],
) -> None:
    """Reply at most once per comment, recording each reply before the next.

    A reply is public the moment it posts, so the mark that stops it posting
    again has to be durable before anything else can fail. Recording the whole
    batch at the end meant one failed reply -- a rate limit, a dropped
    connection -- discarded the marks for every reply already posted, and the
    next completion replied to all of them again. A failure now costs at most
    the comment it happened on, and the others stay answered exactly once.
    """
    answered = {
        actions.str_or_empty(row.get("id"))
        for row in comments
        if row.get("reply_posted_at")
    }
    for row in list(comments):
        pass_number = row.get("addressed_in_pass")
        if not isinstance(pass_number, int) or row.get("reply_posted_at"):
            continue
        # By upstream id, not by row: two rows can name the same comment (a
        # merge that appends rather than upserts, an import), and answering
        # each of them puts two identical replies on one thread.
        if actions.str_or_empty(row.get("id")) in answered:
            continue
        comment = _domain_comment(row)
        if comment is None:
            continue
        note = _addressed_instruction(loop, comment.id)
        body = _addressed_body(
            loop,
            pass_number,
            note,
            actions.str_or_empty(row.get("addressed_note")),
        )
        try:
            reply = await reply_to_pr_comment(gateway, ref, comment, body)
        except Exception:
            # One unreachable comment must not stop the rest being answered,
            # and must not roll back the ones already recorded.
            continue
        if reply is None:
            continue
        posted_at = actions.now_iso()
        row["reply_posted_at"] = posted_at
        answered.add(actions.str_or_empty(row.get("id")))
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
                # Ours, not the user's. Both are authored by the token so both
                # come back `is_viewer`, but only a reply a person typed is an
                # answer that should retire the thread.
                "atelier_reply": True,
            }
        )
        try:
            checkpoint()
        except Exception:
            # The reply is already public. Losing the run's other marks because
            # this write failed would re-post every one of them, so the batch
            # stops here and leaves the rest unanswered rather than duplicated.
            return


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
