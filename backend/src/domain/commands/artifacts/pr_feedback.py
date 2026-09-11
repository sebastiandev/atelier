"""Story PR tracking: refresh a PR's threads and route comments to its opener."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.domain.agents.launch import AgentLaunchRequest, launch_agent
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.agents.resume_runtime import ResumeAgentRequest, resume_agent
from src.domain.artifacts import lifecycle as lc
from src.domain.artifacts.models import PrArtifact
from src.domain.artifacts.pr_status import (
    PrLifecycleGateway,
    fetch_pr_lifecycle,
    parse_pr_url,
    reply_to_pr_comment,
)
from src.domain.connections import ConnectionStore
from src.domain.loop.pr_review import domain_comment, merge_comments
from src.domain.models import Agent
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class PrArtifactNotFound(ValueError):
    """No PR artifact with that slug under the Work."""


class PrUnavailable(ValueError):
    """The pull request could not be read from its provider."""


class OpenerUnknown(ValueError):
    """The PR has no opener snapshot, so nobody can be sent its comments."""


class FeedbackInvalid(ValueError):
    """The selection does not name any known comment."""


@dataclass(frozen=True)
class PrView:
    """What the story PR view renders."""

    artifact: PrArtifact
    lifecycle: dict[str, Any]
    opener: Agent | None


@dataclass(frozen=True)
class RefreshRequest:
    work_slug: str
    artifact_slug: str
    force: bool = False


@dataclass(frozen=True)
class SendFeedbackRequest:
    work_slug: str
    artifact_slug: str
    mode: lc.FeedbackMode
    comment_ids: tuple[str, ...] = ()
    instructions: dict[str, str] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class SendFeedbackResult:
    view: PrView
    target_slug: str
    relaunched: bool


@dataclass(frozen=True)
class LaunchDeps:
    """Everything a relaunch of a removed opener needs."""

    worktree_manager: WorktreeManager
    connection_store: ConnectionStore
    sharestore: SharedFolderStore
    share_provisioner: ShareProvisioner
    adapter_factory: AgentAdapterFactory
    settings: Settings


def get_view(workstore: WorkStore, work_slug: str, artifact_slug: str) -> PrView:
    """Read the stored tracking state without touching the provider.

    Preconditions: the artifact is a PR under ``work_slug``.
    Postconditions: nothing is written.
    """
    artifact = _pr_or_raise(workstore, work_slug, artifact_slug)
    return PrView(
        artifact=artifact,
        lifecycle=dict(artifact.lifecycle or {}),
        opener=_opener(workstore, work_slug, artifact),
    )


async def refresh(
    workstore: WorkStore,
    gateway: PrLifecycleGateway,
    req: RefreshRequest,
) -> PrView:
    """Fetch the PR's lifecycle and threads, then answer pushed feedback.

    Preconditions: the artifact is a PR with a parseable GitHub URL.
    Postconditions: ``lifecycle`` on the row is current; every implement
    batch whose push has landed has one reply posted per comment, recorded
    before the next reply is attempted.
    """
    artifact = _pr_or_raise(workstore, req.work_slug, req.artifact_slug)
    ref = parse_pr_url(artifact.url)
    if ref is None:
        raise PrUnavailable("artifact has no supported pull request URL")
    lifecycle = dict(artifact.lifecycle or {})
    etag = str((lifecycle.get("pr") or {}).get("etag") or "") or None
    fetched = await fetch_pr_lifecycle(gateway, ref, if_none_match=etag, force=req.force)
    if fetched is None:
        raise PrUnavailable("pull-request status is unavailable")
    if fetched.lifecycle is not None:
        lifecycle = lc.apply_fetch(
            lifecycle,
            fetched.lifecycle,
            url=artifact.url,
            ref=ref,
            etag=fetched.etag,
            merge_comments=merge_comments,
        )
        workstore.update_artifact_status(
            req.artifact_slug, fetched.lifecycle.status, pr_etag=fetched.etag
        )
    else:
        lifecycle = lc.touch_synced(lifecycle, url=artifact.url, ref=ref)
    assert artifact.slug is not None
    workstore.update_pr_artifact_lifecycle(artifact.slug, lifecycle)

    await _post_replies(workstore, gateway, artifact.slug, ref, lifecycle)
    return get_view(workstore, req.work_slug, req.artifact_slug)


async def send_feedback(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    deps: LaunchDeps,
    req: SendFeedbackRequest,
) -> SendFeedbackResult:
    """Send selected comments to the agent that opened the PR.

    The target is never a choice: it is the opener. If the opener still
    exists it is resumed when needed and messaged; if it was removed (or
    stopped), a new agent with the snapshot's specs is launched on the PR's
    branch, the artifact is re-pointed at it, and it receives the message.

    Preconditions: the PR has an ``opener_spec``; at least one selected
    comment is a known thread.
    Postconditions: the target agent received one message; the batch is
    recorded on the row; ``artifacts.agent_id`` names the target.
    """
    artifact = _pr_or_raise(workstore, req.work_slug, req.artifact_slug)
    lifecycle = dict(artifact.lifecycle or {})
    spec = lifecycle.get("opener_spec")
    if not isinstance(spec, dict) or not spec.get("provider"):
        raise OpenerUnknown("this pull request does not remember the agent that opened it")
    known = {
        str(row.get("id")) for row in lifecycle.get("comments") or [] if isinstance(row, dict)
    }
    comment_ids = [cid for cid in req.comment_ids if cid in known]
    if not comment_ids:
        raise FeedbackInvalid("select at least one comment")

    opener = _opener(workstore, req.work_slug, artifact)
    relaunched = False
    if opener is None or opener.status in ("stopped", "detached"):
        opener = await _relaunch_opener(workstore, supervisor, deps, req.work_slug, spec, lifecycle)
        assert artifact.slug is not None and opener.slug is not None
        workstore.update_artifact_agent(artifact.slug, opener.slug)
        relaunched = True
    assert opener.slug is not None
    if not supervisor.is_registered(opener.slug):
        await resume_agent(
            workstore,
            supervisor,
            deps.worktree_manager,
            deps.sharestore,
            deps.share_provisioner,
            deps.settings,
            ResumeAgentRequest(work_slug=req.work_slug, agent_slug=opener.slug),
        )

    prompt = lc.feedback_prompt(
        lifecycle,
        comment_ids=comment_ids,
        instructions=req.instructions,
        note=req.note,
        mode=req.mode,
    )
    await supervisor.send_input(opener.slug, prompt)
    lifecycle = lc.record_feedback(
        lifecycle,
        comment_ids=comment_ids,
        instructions=req.instructions,
        note=req.note,
        mode=req.mode,
        sent_to=opener.slug,
    )
    assert artifact.slug is not None
    workstore.update_pr_artifact_lifecycle(artifact.slug, lifecycle)
    return SendFeedbackResult(
        view=get_view(workstore, req.work_slug, req.artifact_slug),
        target_slug=opener.slug,
        relaunched=relaunched,
    )


async def _relaunch_opener(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    deps: LaunchDeps,
    work_slug: str,
    spec: dict[str, Any],
    lifecycle: dict[str, Any],
) -> Agent:
    branch = str((lifecycle.get("pr") or {}).get("branch") or "") or None
    return await launch_agent(
        workstore,
        supervisor,
        deps.worktree_manager,
        deps.connection_store,
        deps.sharestore,
        deps.share_provisioner,
        deps.adapter_factory,
        AgentLaunchRequest(
            work_slug=work_slug,
            name=str(spec.get("name") or "Agent"),
            persona=spec.get("persona") or "developer",
            role=str(spec.get("role") or ""),
            provider=spec["provider"],
            model=str(spec.get("model") or ""),
            folder=Path(str(spec.get("folder") or ".")).expanduser(),
            options=dict(spec.get("options") or {}),
            branch_name=branch,
            artifact_id=spec.get("artifact_id") or None,
        ),
    )


async def _post_replies(
    workstore: WorkStore,
    gateway: PrLifecycleGateway,
    artifact_slug: str,
    ref: Any,
    lifecycle: dict[str, Any],
) -> None:
    """Reply once per pushed implement comment, saving after each post."""
    pending = lc.pending_replies(lifecycle)
    if not pending:
        return
    rows = [dict(row) for row in lifecycle.get("comments") or [] if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in rows}
    touched_batches: list[dict[str, Any]] = []
    for batch, comment_id, instruction in pending:
        row = by_id.get(comment_id)
        if row is None or row.get("reply_posted_at"):
            continue
        comment = domain_comment(row)
        if comment is None:
            continue
        try:
            reply = await reply_to_pr_comment(
                gateway, ref, comment, lc.addressed_body(lifecycle, instruction)
            )
        except Exception:
            continue
        if reply is None:
            continue
        posted = lc.now_iso()
        row["reply_posted_at"] = posted
        rows.append(
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
                "reply_posted_at": posted,
                "atelier_reply": True,
            }
        )
        if batch not in touched_batches:
            touched_batches.append(batch)
        lifecycle["comments"] = rows
        workstore.update_pr_artifact_lifecycle(artifact_slug, lifecycle)
    for batch in touched_batches:
        batch["replied_at"] = lc.now_iso()
    if touched_batches:
        lifecycle["comments"] = rows
        workstore.update_pr_artifact_lifecycle(artifact_slug, lifecycle)


def _pr_or_raise(workstore: WorkStore, work_slug: str, artifact_slug: str) -> PrArtifact:
    artifact = workstore.get_artifact_by_slug(artifact_slug)
    record = workstore.get_work(work_slug)
    if (
        artifact is None
        or record is None
        or not isinstance(artifact, PrArtifact)
        or artifact.work_id != record.work.id
    ):
        raise PrArtifactNotFound(f"pull request artifact not found: {artifact_slug}")
    return artifact


def _opener(workstore: WorkStore, work_slug: str, artifact: PrArtifact) -> Agent | None:
    if artifact.agent_id is None:
        return None
    return next(
        (a for a in workstore.list_agents_for_work(work_slug) if a.id == artifact.agent_id),
        None,
    )


__all__ = [
    "FeedbackInvalid",
    "LaunchDeps",
    "OpenerUnknown",
    "PrArtifactNotFound",
    "PrUnavailable",
    "PrView",
    "RefreshRequest",
    "SendFeedbackRequest",
    "SendFeedbackResult",
    "get_view",
    "refresh",
    "send_feedback",
]
