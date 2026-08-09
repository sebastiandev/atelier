"""Pure state actions for the pull-request stage lifecycle."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.domain.agents.specs import SPECS
from src.domain.artifacts.models import PrArtifact
from src.domain.artifacts.pr_status import parse_pr_url
from src.domain.loop import actions, feedback
from src.domain.loop.agent_policy import sanitize_agent_policy
from src.domain.loop.dtos import (
    PREVIOUS_STAGE,
    LoopAgentPolicy,
    LoopRunStatus,
    LoopStatus,
    LoopStepStatus,
    PrStage,
)
from src.domain.loop.models import LoopRunTarget
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.loop.dtos import LoopStepDefinition


PR_SOURCES = frozenset({"pr_comment", "pr_general"})
"""The feedback sources a pull-request pass is started from."""


CREATE_PR_STAGE_INSTRUCTIONS = (
    "Create or update the single pull request for this Work. Your job is exactly: "
    "stage, commit, push, open or update the PR, and reply to the review comments "
    "this pass addressed — each reply citing the commit that addressed it. Nothing "
    "else. "
    "Do not run the test suite or any individual test. The implementation and code "
    "review stages already ran them and their evidence is in this run's reports; "
    "re-running them here costs time and tokens and proves nothing new. "
    "The only changes you may make are what a commit hook demands: deterministic "
    "formatter and lint fixes, and minimal type-check fixes introduced by the current "
    "diff when they preserve intended behavior and public contracts. Do not blindly "
    "silence checks with ignores, noqa comments, weakened types, or unsafe casts, and "
    "make no other implementation changes. "
    "If anything needs more than that — a failing hook you cannot satisfy within that "
    "scope, or a fix that needs behavioral, architectural, or public-contract changes "
    "— report changes_requested with the failing command, its output, and actionable "
    "findings, and let the implementation stage handle it. "
    "Never bypass hooks or force-push."
)


class PrStageInvalid(ValueError):
    """The requested PR-stage operation is not valid for this run."""


@dataclass(frozen=True)
class PrSetup:
    """User-selected inputs persisted for every PR pass in one run."""

    name: str
    description_mode: str = "automatic"
    description_instructions: str = ""
    manual_body: str = ""
    status: str = "draft"
    base_branch: str = "master"
    branch_name: str = ""
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    fast: bool | None = None


@dataclass(frozen=True)
class PrFeedbackItem:
    """One selected PR comment and optional implementation instruction."""

    comment_id: str
    instruction: str = ""


def snapshot_stage_config(
    target: LoopRunTarget,
    stage: LoopStepDefinition,
) -> None:
    """Pin reusable Create-PR setup when its stage enters a run.

    Preconditions: ``stage`` is the PR stage being entered. Postconditions: a
    reusable stage supplies one resolved runtime config only when the run does
    not already own authoritative one-off setup.
    """
    loop = actions.dict_or_empty(target.run.get("loop"))
    if isinstance(loop.get("pr_config"), dict) or not isinstance(stage, PrStage):
        return
    brief = actions.dict_or_empty(target.run.get("brief"))
    goal = actions.str_or_empty(brief.get("goal")) or target.title
    config = stage.pr_config
    template = config.name_template
    loop["pr_config"] = {
        "name": template.replace("{goal}", goal).replace("{work-id}", target.work_slug),
        "name_template": template,
        "description_mode": config.description_mode,
        "description_instructions": config.description_instructions,
        "manual_body": config.manual_body,
        "status": config.status,
        "base_branch": config.base_branch,
        "branch_name": config.branch_name,
    }
    target.run["loop"] = loop


def inherit_existing_pr(
    loop: dict[str, Any],
    source_loop: dict[str, Any],
) -> None:
    """Carry stable PR state into a follow-up run without transient pass state.

    Preconditions: ``loop`` is fresh and ``source_loop`` belongs to its source
    run. Postconditions: an existing PR, its setup, and review comments are
    copied by value; pending feedback and prior pass history are not copied.
    """
    source_pr = actions.dict_or_empty(source_loop.get("pr"))
    if not actions.str_or_empty(source_pr.get("url")):
        return
    loop["pr"] = deepcopy(source_pr)
    source_config = source_loop.get("pr_config")
    if isinstance(source_config, dict):
        loop["pr_config"] = deepcopy(source_config)
    source_comments = source_loop.get("pr_comments")
    if isinstance(source_comments, list):
        loop["pr_comments"] = deepcopy(source_comments)


def add_one_off_stage(target: LoopRunTarget, setup: PrSetup) -> None:
    """Append and schedule a Work-local PR stage on an accepted run.

    Preconditions: the pinned loop is accepted, has no PR stage, and ends at a
    user approval. Postconditions: the run owns a new snapshot revision and its
    monitor is ready to launch the PR stage in the retained workspace.
    """
    _validate_setup(setup)
    run = target.run
    loop = actions.dict_or_empty(run.get("loop"))
    if actions.loop_status(loop.get("status"), actions.run_status(run)) != LoopStatus.ACCEPTED:
        raise PrStageInvalid("Create PR is available after the run is approved")
    snapshot = actions.dict_or_empty(loop.get("definition_snapshot"))
    raw_stages = snapshot.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise PrStageInvalid("run has no pinned loop stages")
    if any(isinstance(stage, dict) and stage.get("kind") == "pr" for stage in raw_stages):
        raise PrStageInvalid("run already contains a Create PR stage")

    approval_id = actions.str_or_empty(loop.get("current_stage_id"))
    approval = next(
        (
            stage
            for stage in raw_stages
            if isinstance(stage, dict)
            and stage.get("id") == approval_id
            and stage.get("kind") == "user_approval"
        ),
        None,
    )
    if approval is None:
        raise PrStageInvalid("one-off Create PR requires a final approval stage")
    implementation = next(
        (
            stage
            for stage in raw_stages
            if isinstance(stage, dict) and stage.get("kind") == "agent_task"
        ),
        None,
    )
    if implementation is None:
        raise PrStageInvalid("Create PR requires an implementation stage")

    stage_id = _unique_stage_id(raw_stages, "create-pr")
    config = _setup_snapshot(setup)
    agent = deepcopy(implementation.get("agent"))
    if not isinstance(agent, dict):
        agent = {}
    agent.update(
        {
            "session": "fresh",
            "permissions": "write",
            "provider": setup.provider or agent.get("provider"),
            "model": setup.model or agent.get("model"),
            "effort": setup.effort or agent.get("effort"),
            "fast": setup.fast if setup.fast is not None else agent.get("fast"),
            "approved_command_prefixes": _approved_prefixes(agent),
        }
    )
    provider = agent.get("provider")
    if isinstance(provider, str) and provider in SPECS:
        model = agent.get("model")
        sanitized = sanitize_agent_policy(
            LoopAgentPolicy(
                provider=provider,
                model=model if isinstance(model, str) else None,
                effort=agent.get("effort") if isinstance(agent.get("effort"), str) else None,
                fast=agent.get("fast") if isinstance(agent.get("fast"), bool) else None,
            ),
            provider=provider,
            model=model if isinstance(model, str) else None,
        )
        if sanitized.effort is None:
            agent.pop("effort", None)
        if sanitized.fast is None:
            agent.pop("fast", None)
    raw_stages.append(
        {
            "id": stage_id,
            "name": "Create PR",
            "kind": "pr",
            "instructions": _pr_instructions(setup),
            "context": [
                {"kind": "workspace_diff", "required": True, "paths": []},
                {"kind": "changed_files", "required": True, "paths": []},
            ],
            # Declared in the current shape. Emitting the old one worked only
            # because the compatibility reader converts it, which is not
            # something a domain writer should be relying on.
            "reports": [{"from": PREVIOUS_STAGE, "required": True}],
            "agent": agent,
            "report_contract": "implementation",
            "retry": {"max_attempts": 2, "timeout_minutes": 20},
            "transitions": {
                "pass": "complete",
                "changes_requested": str(implementation.get("id")),
                "blocked_user": "pause",
                "failed": "fail",
            },
            "check_adapter": None,
            "check_command": [],
            "note_required": False,
            "pr_config": config,
        }
    )
    transitions = approval.setdefault("transitions", {})
    if not isinstance(transitions, dict):
        raise PrStageInvalid("approval stage has invalid transitions")
    transitions["pass"] = stage_id
    revision = _snapshot_revision(snapshot)
    snapshot["revision"] = revision
    snapshot["scope"] = "work"
    loop["definition_revision"] = revision
    loop["definition_snapshot"] = snapshot
    loop["pr_config"] = config
    loop["pass_number"] = max(1, actions.int_or_default(loop.get("pass_number"), 1))
    rows = loop.get("stages")
    if not isinstance(rows, list):
        raise PrStageInvalid("run stage state is incomplete")
    rows.append(
        {
            "id": stage_id,
            "name": "Create PR",
            "kind": "pr",
            "status": LoopStepStatus.PENDING.value,
            "attempt": 0,
            "max_attempts": 2,
            "agent_slug": None,
            "permissions": "write",
            "session": "fresh",
            "summary": "",
            "findings": [],
        }
    )
    loop["approval_decision"] = {"summary": "Approved by user."}
    loop["status"] = LoopStatus.NEEDS_AGENT.value
    loop["status_reason"] = "Approval recorded; starting Create PR."
    run["status"] = LoopRunStatus.RUNNING.value
    run["completed_at"] = None
    run["accepted_at"] = None
    run["loop"] = loop


def prepare_feedback(
    target: LoopRunTarget,
    items: tuple[PrFeedbackItem, ...],
    instruction: str,
) -> None:
    """Schedule selected PR feedback for a fresh implementation pass.

    Preconditions: either an opened PR pass is accepted with visible,
    unaddressed comments, or a failed PR stage has a recovery instruction.
    Postconditions: pass N+1 is running and the monitor has one durable bundle
    to route through the PR stage's return edge.
    """
    run = target.run
    loop = actions.dict_or_empty(run.get("loop"))
    pr = actions.dict_or_empty(loop.get("pr"))
    pr_stage = _latest_pr_stage(loop)
    # A stored decision is a pass this run has scheduled but not yet entered.
    # The state checks below are what stop a *running* pass from being asked
    # for a second one; an unpushed request is not that signal, because the
    # push it was waiting for may have failed or its pull request may have been
    # closed, and either way the user still needs a way forward.
    if isinstance(loop.get("pr_feedback_decision"), dict):
        raise PrStageInvalid("a pull-request feedback pass is already in progress")
    failed_pr = (
        actions.run_status(run) == LoopRunStatus.BLOCKED
        and actions.loop_status(loop.get("status"), actions.run_status(run)) == LoopStatus.FAILED
        and pr_stage.get("status") == LoopStepStatus.FAILED.value
    )
    if not failed_pr and (
        actions.run_status(run) != LoopRunStatus.ACCEPTED
        or actions.loop_status(loop.get("status"), actions.run_status(run)) != LoopStatus.ACCEPTED
        or pr_stage.get("status") != LoopStepStatus.PASSED.value
    ):
        raise PrStageInvalid("pull-request feedback is available only after Create PR completes")
    if failed_pr and (items or not instruction.strip()):
        raise PrStageInvalid("failed Create PR can return to implementation with an instruction")
    if not failed_pr and not actions.str_or_empty(pr.get("url")):
        raise PrStageInvalid("run has no pull request")
    if not items and not instruction.strip():
        raise PrStageInvalid("select a comment or add an instruction")
    comments = loop.get("pr_comments")
    comment_rows = comments if isinstance(comments, list) else []
    by_id = {
        actions.str_or_empty(comment.get("id")): comment
        for comment in comment_rows
        if isinstance(comment, dict)
    }
    selected: list[dict[str, Any]] = []
    for item in items:
        comment = by_id.get(item.comment_id)
        thread = _comment_thread(comment_rows, comment) if comment is not None else []
        latest = thread[-1] if thread else None
        if (
            comment is None
            or latest is not comment
            or comment.get("is_viewer") is True
            or comment.get("addressed_in_pass") is not None
            or not _created_after(comment.get("created_at"), pr_stage.get("push_at"))
        ):
            raise PrStageInvalid(f"PR comment is unavailable: {item.comment_id}")
        root = thread[0]
        selected.append(
            {
                "ref": item.comment_id,
                "author": actions.str_or_empty(root.get("author")),
                "location": actions.str_or_empty(root.get("location")),
                "body": actions.str_or_empty(root.get("body")),
                "instruction": item.instruction.strip(),
                "thread": [
                    {
                        "author": actions.str_or_empty(row.get("author")),
                        "body": actions.str_or_empty(row.get("body")),
                        "created_at": actions.str_or_empty(row.get("created_at")),
                        "is_viewer": row.get("is_viewer") is True,
                    }
                    for row in thread
                ],
            }
        )
    pass_number = actions.start_next_pass(loop)
    pr_number = pr.get("number")
    ref = parse_pr_url(actions.str_or_empty(pr.get("url")))
    number = (
        pr_number
        if isinstance(pr_number, int) and not isinstance(pr_number, bool)
        else ref.number
        if ref is not None
        else 0
    )
    reason = "after Create PR failure" if failed_pr else f"from PR #{number} feedback"
    _reset_pass_occurrences(loop)
    # The review that will inspect the answer is what discharges this request,
    # not the PR stage: making the push itself the answer deadlocked the run,
    # because the PR stage could not pass while its own feedback was open.
    entry = feedback.record(
        loop,
        source="pr_comment" if selected else "pr_general",
        note=instruction,
        items=tuple(selected),
        pass_number=pass_number,
        answered_by=_verifying_stage_id(loop, pr_stage),
        reason=reason,
    )
    loop["pr_feedback_decision"] = {
        "stage_id": actions.str_or_empty(pr_stage.get("id")),
        "summary": feedback.render(entry) if entry is not None else "",
    }
    loop["status"] = LoopStatus.NEEDS_AGENT.value
    loop["status_reason"] = f"Starting implementation pass {pass_number} from PR feedback."
    loop.pop("failure_kind", None)
    run["status"] = LoopRunStatus.RUNNING.value
    run["completed_at"] = None
    run["accepted_at"] = None
    run["loop"] = loop


def capture_completion(
    workstore: WorkStore,
    target: LoopRunTarget,
    stage_row: dict[str, Any],
    artifact_refs: tuple[str, ...],
) -> None:
    """Capture one successful PR push and seal its current pass.

    Preconditions: a PR stage emitted a passing report. Postconditions: the
    stage owns its push timestamp and PR snapshot; scheduled feedback becomes
    addressed on this pass and the pass is sealed for read-only history.
    """
    loop = actions.dict_or_empty(target.run.get("loop"))
    existing = actions.dict_or_empty(loop.get("pr"))
    url, artifact = _resolve_pr_completion(
        workstore,
        target.work_slug,
        stage_row,
        artifact_refs,
        actions.str_or_empty(existing.get("url")),
    )
    ref = parse_pr_url(url)
    config = actions.dict_or_empty(loop.get("pr_config"))
    now = actions.now_iso()
    pr = {
        **existing,
        "url": url,
        "number": ref.number if ref is not None else existing.get("number"),
        "title": (
            artifact.title if artifact is not None else actions.str_or_empty(config.get("name"))
        ),
        "branch": actions.str_or_empty(config.get("branch_name")),
        "base": actions.str_or_empty(config.get("base_branch")) or "master",
        "status": artifact.status if artifact is not None else config.get("status", "draft"),
        "checks": existing.get("checks", {"passed": 0, "total": 0, "state": "pending"}),
        "review_state": existing.get("review_state", "pending"),
        "last_synced_at": existing.get("last_synced_at"),
    }
    pass_number = max(1, actions.int_or_default(loop.get("pass_number"), 1))
    stage_row["push_at"] = now
    # Everything asked since the last push, whether or not the review has
    # already answered it: pushing is not what answers PR feedback, and a
    # send-back between the request and the push moves the pass counter on.
    carried = _unpushed_feedback(loop)
    addressed_rows = [
        {
            "comment_id": actions.str_or_empty(item.get("ref")),
            "author": actions.str_or_empty(item.get("author")),
            "location": actions.str_or_empty(item.get("location")),
            "body": actions.str_or_empty(item.get("body")),
            "instruction": actions.str_or_empty(item.get("instruction")),
            "thread": item.get("thread", []),
        }
        for entry in carried
        for item in entry["items"]
    ]
    stage_row["addressed_comments"] = addressed_rows
    stage_row["feedback_instruction"] = "\n\n".join(
        entry["note"] for entry in carried if entry["note"]
    )
    if addressed_rows:
        addressed_ids = {item["comment_id"] for item in addressed_rows}
        comments = loop.get("pr_comments")
        if isinstance(comments, list):
            for comment in comments:
                if isinstance(comment, dict) and comment.get("id") in addressed_ids:
                    comment["addressed_in_pass"] = pass_number
    for entry in carried:
        feedback.update(loop, entry["id"], pushed_in_pass=pass_number)
    loop["pr"] = pr
    started_at = (
        (carried[0]["created_at"] if carried else "")
        or actions.str_or_empty(target.run.get("started_at"))
        or now
    )
    reason = (carried[0]["reason"] if carried else "") or "initial"
    passes = loop.setdefault("passes", [])
    if isinstance(passes, list):
        sealed = {
            "number": pass_number,
            "started_at": started_at,
            "sealed_at": now,
            "reason": reason,
            "elapsed_seconds": _elapsed_seconds(started_at, now),
            "pr_url": url,
            "pr_number": pr.get("number"),
            "addressed_comments": addressed_rows,
        }
        existing_pass = next(
            (
                item
                for item in passes
                if isinstance(item, dict) and item.get("number") == pass_number
            ),
            None,
        )
        if existing_pass is not None:
            existing_pass.update(sealed)
        else:
            passes.append(sealed)
    target.run["loop"] = loop


def prompt_context(run: dict[str, Any]) -> str:
    """Return persisted PR setup and feedback for a fresh PR-stage agent."""
    loop = actions.dict_or_empty(run.get("loop"))
    config = actions.dict_or_empty(loop.get("pr_config"))
    pr = actions.dict_or_empty(loop.get("pr"))
    lines = [
        "PR setup:",
        f"- title: {actions.str_or_empty(config.get('name'))}",
        f"- status: {actions.str_or_empty(config.get('status')) or 'draft'}",
        f"- base: {actions.str_or_empty(config.get('base_branch')) or 'master'}",
    ]
    if config.get("description_mode") == "manual":
        lines.extend(
            (
                "- description: use this exact Markdown:",
                actions.str_or_empty(config.get("manual_body")),
            )
        )
    else:
        lines.append("- description: draft it from the complete run report")
        guidance = actions.str_or_empty(config.get("description_instructions"))
        if guidance:
            lines.append(f"- description guidance: {guidance}")
    branch = actions.str_or_empty(config.get("branch_name"))
    if branch:
        lines.append(f"- branch: {branch}")
    else:
        lines.append(
            "- branch: reuse the current branch; if HEAD is detached, return "
            "blocked_user and ask for a branch name"
        )
    url = actions.str_or_empty(pr.get("url"))
    if url:
        lines.extend(
            (
                f"Existing PR: {url}",
                "Update this PR by committing and pushing; do not create another PR.",
            )
        )
    # What this push answers, in the past tense: the PR body and the replies to
    # each comment are written from it. The live instruction reaches the stage
    # through the shared feedback renderer, so it is not repeated here.
    carried = _unpushed_feedback(loop)
    comments = [item for entry in carried for item in entry["items"]]
    if comments:
        lines.append("Addressed in this push:")
        for comment in comments:
            instruction = actions.str_or_empty(comment.get("instruction"))
            lines.append(
                "- "
                + actions.str_or_empty(comment.get("body"))
                + (f" User instruction: {instruction}" if instruction else "")
            )
    for entry in carried:
        if entry["note"]:
            lines.append(f"User instruction: {entry['note']}")
    return "\n".join(lines)


def _validate_setup(setup: PrSetup) -> None:
    if not setup.name.strip():
        raise PrStageInvalid("PR name is required")
    if setup.description_mode not in {"automatic", "manual"}:
        raise PrStageInvalid("PR description mode must be automatic or manual")
    if setup.description_mode == "manual" and not setup.manual_body.strip():
        raise PrStageInvalid("manual PR description is required")
    if setup.status not in {"draft", "open"}:
        raise PrStageInvalid("PR status must be draft or open")
    if not setup.base_branch.strip():
        raise PrStageInvalid("PR base branch is required")


def _setup_snapshot(setup: PrSetup) -> dict[str, str]:
    return {
        "name": setup.name.strip(),
        "name_template": setup.name.strip(),
        "description_mode": setup.description_mode,
        "description_instructions": setup.description_instructions.strip(),
        "manual_body": setup.manual_body.strip(),
        "status": setup.status,
        "base_branch": setup.base_branch.strip(),
        "branch_name": setup.branch_name.strip(),
    }


def _pr_instructions(setup: PrSetup) -> str:
    description = (
        f"Use this exact Markdown description:\n{setup.manual_body.strip()}"
        if setup.description_mode == "manual"
        else "Draft the description from the complete run report."
    )
    if setup.description_instructions.strip():
        description += (
            f"\nAdditional description guidance: {setup.description_instructions.strip()}"
        )
    state = "--draft" if setup.status == "draft" else "as an open pull request"
    branch = (
        f"Create and push branch {setup.branch_name.strip()}."
        if setup.branch_name.strip()
        else (
            "Reuse the current branch; if HEAD is detached, report blocked_user "
            "and ask for a branch name."
        )
    )
    return (
        f"{CREATE_PR_STAGE_INSTRUCTIONS} "
        f"Use title {setup.name.strip()!r}, base {setup.base_branch.strip()!r}, {state}. "
        f"{branch} {description} If a PR already exists in the supplied context, update "
        "that PR and never create a second one. Record it with atelier__record_pr and "
        "include its URL in artifact_refs."
    )


def _approved_prefixes(agent: dict[str, Any]) -> list[str]:
    raw = agent.get("approved_command_prefixes")
    prefixes = [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
    for value in ("git add", "git commit"):
        if value not in prefixes:
            prefixes.append(value)
    return prefixes


def _unique_stage_id(stages: list[object], base: str) -> str:
    ids = {str(stage.get("id")) for stage in stages if isinstance(stage, dict) and stage.get("id")}
    if base not in ids:
        return base
    suffix = 2
    while f"{base}-{suffix}" in ids:
        suffix += 1
    return f"{base}-{suffix}"


def _snapshot_revision(snapshot: dict[str, Any]) -> str:
    value = deepcopy(snapshot)
    value.pop("revision", None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:8]


def _latest_pr_stage(loop: dict[str, Any]) -> dict[str, Any]:
    rows = loop.get("stages")
    if not isinstance(rows, list):
        raise PrStageInvalid("run stage state is incomplete")
    stage = next(
        (row for row in reversed(rows) if isinstance(row, dict) and row.get("kind") == "pr"),
        None,
    )
    if stage is None:
        raise PrStageInvalid("run has no Create PR stage")
    return stage


def _unpushed_feedback(loop: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the pull-request requests no push has carried yet, oldest first."""
    return [
        entry
        for entry in feedback.from_sources(loop, PR_SOURCES)
        if entry.get("pushed_in_pass") is None
    ]


def _verifying_stage_id(loop: dict[str, Any], pr_stage: dict[str, Any]) -> str:
    """Return the stage whose pass discharges pull-request feedback.

    The code review is what inspects the answer, so it is what answers the
    request. A loop without one falls back to its approval stage -- the user
    saying the result is good is a real verification of whether the feedback
    was addressed, and every loop is validated to have one.

    Never the PR stage. Binding the record to the stage that publishes is
    exactly the coupling this spec removed: the PR stage would have had to pass
    to discharge feedback it was itself being handed, and a publishing stage
    cannot act on a change request.
    """
    rows = [row for row in loop.get("stages", []) if isinstance(row, dict)]
    verifier = next(
        (row for row in reversed(rows) if row.get("kind") == "agent_review"),
        None,
    ) or next(
        (row for row in reversed(rows) if row.get("kind") == "user_approval"),
        None,
    )
    return actions.str_or_empty((verifier or pr_stage).get("id"))


def _comment_thread(comments: list[object], selected: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one chronological GitHub review thread for feedback validation."""
    kind = actions.str_or_empty(selected.get("kind"))
    target = actions.str_or_empty(selected.get("reply_target_id"))
    selected_id = actions.str_or_empty(selected.get("id"))
    rows = [
        row
        for row in comments
        if isinstance(row, dict)
        and (
            actions.str_or_empty(row.get("id")) == selected_id
            if kind != "review" or not target
            else actions.str_or_empty(row.get("kind")) == "review"
            and actions.str_or_empty(row.get("reply_target_id")) == target
        )
    ]
    return sorted(rows, key=lambda row: actions.str_or_empty(row.get("created_at")))


def _resolve_pr_completion(
    workstore: WorkStore,
    work_slug: str,
    stage_row: dict[str, Any],
    artifact_refs: tuple[str, ...],
    existing_url: str,
) -> tuple[str, PrArtifact | None]:
    """Resolve one PR completion and reject a second pull request."""
    rows = [
        artifact
        for artifact in workstore.list_artifacts_for_work(work_slug)
        if isinstance(artifact, PrArtifact)
    ]
    agent_slug = actions.str_or_empty(stage_row.get("agent_slug"))
    agent = next(
        (item for item in workstore.list_agents_for_work(work_slug) if item.slug == agent_slug),
        None,
    )
    owned = (
        [artifact for artifact in rows if artifact.agent_id == agent.id]
        if agent is not None and agent.id is not None
        else []
    )
    reported = [(value, ref) for value in artifact_refs if (ref := parse_pr_url(value)) is not None]
    owned_refs = [
        (artifact.url, ref) for artifact in owned if (ref := parse_pr_url(artifact.url)) is not None
    ]
    existing_ref = parse_pr_url(existing_url)
    if existing_url:
        if existing_ref is None:
            raise PrStageInvalid("stored pull request URL is unsupported")
        if any(ref != existing_ref for _, ref in (*reported, *owned_refs)):
            raise PrStageInvalid(f"Create PR must update the existing pull request: {existing_url}")
        candidates = [artifact for artifact in rows if parse_pr_url(artifact.url) == existing_ref]
        return existing_url, _latest_artifact(candidates)

    references = {ref for _, ref in (*reported, *owned_refs)}
    if not references:
        raise PrStageInvalid("Create PR passed without recording or reporting a pull request")
    if len(references) != 1:
        raise PrStageInvalid("Create PR reported more than one pull request")
    selected_ref = next(iter(references))
    selected_url = next(value for value, ref in (*reported, *owned_refs) if ref == selected_ref)
    candidates = [artifact for artifact in rows if parse_pr_url(artifact.url) == selected_ref]
    return selected_url, _latest_artifact(candidates)


def _latest_artifact(artifacts: list[PrArtifact]) -> PrArtifact | None:
    """Return the newest matching PR artifact, if one was recorded."""
    return max(artifacts, key=lambda artifact: artifact.created_at) if artifacts else None


def _reset_pass_occurrences(loop: dict[str, Any]) -> None:
    """Reset occurrence-local counters while preserving append-only reports."""
    rows = loop.get("stages")
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        row["attempt"] = 0
        for key in (
            "auto_approved_permission_ids",
            "connection_recovered_attempt",
            "recovered_attempt",
            "recovered_stale_permission_ids",
        ):
            row.pop(key, None)
    loop["attempt"] = 1


def _created_after(created_at: object, pushed_at: object) -> bool:
    if not isinstance(created_at, str) or not isinstance(pushed_at, str):
        return True
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if pushed.tzinfo is None:
        pushed = pushed.replace(tzinfo=UTC)
    return created > pushed


def _elapsed_seconds(started_at: str, sealed_at: str) -> int:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        sealed = datetime.fromisoformat(sealed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if sealed.tzinfo is None:
        sealed = sealed.replace(tzinfo=UTC)
    return max(0, int((sealed - started).total_seconds()))


__all__ = [
    "CREATE_PR_STAGE_INSTRUCTIONS",
    "PrFeedbackItem",
    "PrSetup",
    "PrStageInvalid",
    "add_one_off_stage",
    "capture_completion",
    "inherit_existing_pr",
    "prepare_feedback",
    "prompt_context",
    "snapshot_stage_config",
]


def has_pull_request(loop: dict[str, Any]) -> bool:
    """Whether the run stored a pull request at all.

    Callers check this before loading artifacts: most runs never reach the PR
    stage, and there is nothing to compare them against.
    """
    return bool(actions.str_or_empty(actions.dict_or_empty(loop.get("pr")).get("url")))


def live_pr_status(loop: dict[str, Any], artifacts: list[PrArtifact]) -> str:
    """Return the polled status of the run's pull request, or ``""``.

    The run's own ``pr`` snapshot only refreshes when someone asks it to, so it
    reports a pull request as open long after GitHub moved on. The artifact rows
    are what the background poller keeps current, and they are already loaded
    from the same store this module reads elsewhere.

    Preconditions: ``loop`` is a loop-run snapshot; ``artifacts`` are the PR
    artifacts of its Work. Postconditions: empty when the run has no pull
    request, its URL is unsupported, or no artifact row matches it.
    """
    ref = parse_pr_url(actions.str_or_empty(actions.dict_or_empty(loop.get("pr")).get("url")))
    if ref is None:
        return ""
    matching = [artifact for artifact in artifacts if parse_pr_url(artifact.url) == ref]
    latest = _latest_artifact(matching)
    return latest.status if latest is not None else ""


def adopt_live_pr_status(loop: dict[str, Any], status: str) -> None:
    """Record the polled status on the run's own snapshot.

    Preconditions: ``status`` is a known PR status. Postconditions: the snapshot
    agrees with the poller, so the run stops reporting a stale state; the rest of
    the snapshot -- checks, comments, head SHA -- is left as the last refresh
    captured it, because only the status was re-derived.
    """
    pr = actions.dict_or_empty(loop.get("pr"))
    if not pr or pr.get("status") == status:
        return
    pr["status"] = status
    loop["pr"] = pr


def clear_for_new_pr(loop: dict[str, Any]) -> None:
    """Drop the pull request so the PR stage opens a fresh one.

    ``_resolve_pr_completion`` forces the stage to update a stored URL and
    rejects any other pull request, so a run whose PR is gone can only proceed
    once the URL is cleared. The reusable setup (``pr_config``) stays: the next
    pull request wants the same base branch, title template, and reviewers.

    Preconditions: ``loop`` is a mutable loop-run snapshot. Postconditions: no
    PR and no PR comments. Feedback records survive as the run's account of
    what was asked, but any still waiting for a push is closed out: the pull
    request it quoted is gone, so no later push can carry it. Leaving them open
    would put comments from a deleted pull request into the next one's push
    summary and seal that pass with the old request's timing and reason.
    """
    for entry in _unpushed_feedback(loop):
        feedback.update(
            loop,
            entry["id"],
            pushed_in_pass=max(1, actions.int_or_default(loop.get("pass_number"), 1)),
        )
    loop.pop("pr", None)
    loop.pop("pr_comments", None)
