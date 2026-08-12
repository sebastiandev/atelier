"""Backend-owned provider prompts for multi-stage loop execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import singledispatch
from typing import Any

from src.domain.loop.dtos import (
    LoopContextKind,
    LoopStepDefinition,
    PrStage,
    ReviewStage,
)
from src.domain.loop.stage_access import stage_instructions


@dataclass(frozen=True)
class StageReportBlock:
    """One earlier stage's account, labelled with whose it is.

    A stage may be given several. Without the label an agent reading two
    reports cannot tell which stage said what, which is worse than one report.
    """

    stage_id: str
    stage_name: str = ""
    pass_number: int = 0
    summary: str = ""
    findings: tuple[str, ...] = ()
    validation_evidence: str = ""


@dataclass(frozen=True)
class StagePromptInput:
    """Context shared by every agent-backed loop stage."""

    run_id: str
    work_slug: str
    artifact_id: str
    artifact_title: str
    source_ref: str
    stage: LoopStepDefinition
    reports: tuple[StageReportBlock, ...] = ()
    history: tuple[str, ...] = ()
    previous_changed_files: tuple[str, ...] = ()
    workspace_diff: str = ""
    resolution_note: str = ""
    resolved_context: tuple[str, ...] = ()
    context_warnings: tuple[str, ...] = ()
    brief_note: str = ""
    brief_context: tuple[str, ...] = ()
    waived_findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskStagePrompt(StagePromptInput):
    """Prompt input for an implementation or corrective write stage."""


@dataclass(frozen=True)
class ReviewStagePrompt(StagePromptInput):
    """Prompt input for an independent read-only review stage."""


@dataclass(frozen=True)
class PrStagePrompt(StagePromptInput):
    """Prompt input for narrow write-capable pull-request preparation."""


@singledispatch
def build_stage_prompt(value: StagePromptInput) -> str:
    """Render the provider prompt for a typed stage input."""
    raise TypeError(f"unsupported loop stage prompt: {type(value).__name__}")


@build_stage_prompt.register
def _task_prompt(value: TaskStagePrompt) -> str:
    return _shared_prompt(
        value,
        posture=(
            "You are the implementation actor. Work in the assigned workspace and "
            "complete the target acceptance contract before reporting pass."
        ),
    )


@build_stage_prompt.register
def _review_prompt(value: ReviewStagePrompt) -> str:
    return _shared_prompt(
        value,
        posture=(
            "You are an independent read-only reviewer. Start with the configured "
            "context and prior evidence, inspect only the files needed to verify changed "
            "behavior, and do not modify files. Reuse reported validation evidence; "
            "run focused checks only when evidence is missing or a finding depends on "
            "them. The complete workspace diff is already included below: do not run "
            "another wholesale `git diff` or combine it with other reads. Read the "
            "target artifact once, then inspect focused source ranges only. Do not "
            "audit unrelated repository history or documentation."
        ),
    )


@build_stage_prompt.register
def _pr_prompt(value: PrStagePrompt) -> str:
    return _shared_prompt(
        value,
        posture=(
            "You are the pull-request preparation actor. Work in the assigned workspace "
            "only within the stage's repair policy and do not broaden implementation "
            "scope. Report changes_requested when a required fix exceeds that policy."
        ),
    )


@singledispatch
def prompt_for(stage: LoopStepDefinition) -> type[StagePromptInput]:
    """Return the prompt a stage gets.

    Dispatches on what the stage *is*. It used to read a `report_contract`
    field, which only ever held the value its kind implied and had to be
    back-filled from that kind on read -- the type was the declaration all
    along.
    """
    return TaskStagePrompt


@prompt_for.register
def _review_prompt_type(stage: ReviewStage) -> type[StagePromptInput]:
    return ReviewStagePrompt


@prompt_for.register
def _pr_prompt_type(stage: PrStage) -> type[StagePromptInput]:
    return PrStagePrompt


def _changes_guidance(stage: LoopStepDefinition) -> str:
    """Return how a stage may use the `changes_requested` outcome."""
    if isinstance(stage, PrStage):
        return (
            "Never use `changes_requested`: publishing is the last decision, and review "
            "findings you cannot act on are not yours to reopen. If you cannot publish, "
            "use `failed`, or `blocked_user` when only the user can unblock it."
        )
    return "Use `changes_requested` only from a review stage."


def build_follow_up_prompt(value: StagePromptInput) -> str:
    """Render the next turn for a stage whose session is being resumed.

    Preconditions: the agent still holds the session that ran this stage, so its
    transcript already carries the seed -- the target, the posture, the stage
    instructions, the history and the diff.
    Postconditions: the prompt carries only what the agent cannot already know:
    reports produced by *other* stages since it last ran, context re-resolved
    for this entry, what is being asked of it now, findings newly taken off the
    table, and the report contract it has to answer in.

    Re-seeding a live session instead is what makes an agent redo settled work:
    the account of what already happened arrives a second time and reads as a
    fresh order. The caller decides what belongs here -- a report block is only
    passed when another stage wrote it, never the agent's own.
    """
    waived = (
        "\n\n## Already dismissed by the user -- leave these alone\n"
        + "\n".join(f"- {item}" for item in value.waived_findings)
        if value.waived_findings
        else ""
    )
    # Written by someone else while this session was idle, so the transcript
    # premise does not cover it.
    elsewhere = "".join(_report_block(report) for report in value.reports)
    since = f"\n## What happened while you waited{elsewhere}" if elsewhere else ""
    asked = _open_requests(value).strip() or "Continue this stage."
    return (
        f"{since}"
        f"{_context_index(value)}\n\n"
        f"{asked}"
        f"{waived}\n\n"
        "When this stage reaches a stopping point, respond with exactly one "
        "single-line JSON report and no Markdown fence:\n"
        f"{_report_example()}\n\n"
        "Use outcome `blocked_user` only for a concrete decision or action that "
        f"only the user can provide. {_changes_guidance(value.stage)} Use explicit "
        "`None.` strings when a text field has no content."
    )


def _shared_prompt(value: StagePromptInput, *, posture: str) -> str:
    context_kinds = {item.kind for item in value.stage.inputs}
    previous = "".join(_report_block(report) for report in value.reports)
    changed_files = ""
    if LoopContextKind.CHANGED_FILES in context_kinds:
        rows = "\n".join(f"- {item}" for item in value.previous_changed_files)
        changed_files = "\n\nChanged files from prior stage reports:\n" + (
            rows if rows else "None reported."
        )
    workspace = ""
    if LoopContextKind.WORKSPACE_DIFF in context_kinds:
        workspace = "\n\nCurrent workspace diff:\n" + (
            value.workspace_diff.strip() or "Workspace state unavailable."
        )
    history = "\n\n" + "\n".join(value.history) if value.history else ""
    # Omitted entirely when there is nothing to say. Claiming "nothing has
    # happened yet" would be a lie to any stage that simply declares no
    # history and no diff -- and a heading with nothing under it is worse than
    # no heading.
    happened = f"{history}{previous}{changed_files}{workspace}"
    already_happened = f"\n## What has already happened{happened}" if happened else ""
    waived = (
        "\n\n## Already dismissed by the user -- leave these alone\n"
        + "\n".join(f"- {item}" for item in value.waived_findings)
        if value.waived_findings
        else ""
    )
    changes_guidance = _changes_guidance(value.stage)
    return (
        f"Execute loop run `{value.run_id}`, stage `{value.stage.step_id}` "
        f"({value.stage.name}) for `{value.artifact_id}`: {value.artifact_title}.\n\n"
        f"Source artifact: {value.source_ref}\n"
        f"Work: {value.work_slug}\n\n"
        "Scope contract: The target artifact and its acceptance criteria are "
        "authoritative. Stage instructions describe how to act; they must not replace "
        "or broaden the target. If they conflict, follow the target and report the "
        "conflict instead of implementing unrelated work.\n\n"
        f"{posture}\n"
        # Three zones, and the boundary between them is the point. Everything
        # above "Your task now" is an account of what already happened, in the
        # past tense; everything below it is what to do. Mixing them is what
        # let a request the user had already withdrawn keep reading as a live
        # order, and what made an agent treat a prior stage's findings as its
        # own instructions.
        f"{already_happened}"
        f"{waived}"
        f"\n\n## Your task now\n{_instructions(value.stage)}"
        f"{_brief(value)}"
        f"{_context_index(value)}"
        f"{_open_requests(value)}\n\n"
        "When this stage reaches a stopping point, respond with exactly one "
        "single-line JSON report and no Markdown fence:\n"
        f"{_report_example()}\n\n"
        "Use outcome `blocked_user` only for a concrete decision or action that "
        f"only the user can provide. {changes_guidance} Use explicit `None.` strings "
        "when a text field has no content.\n\n"
        "Keep `summary` to a short verdict a reader takes in at a glance -- a "
        "few sentences, not a recital. Per-criterion detail belongs in "
        "`criteria`, defects in `findings`, and what you did in `changes`; each "
        "is rendered as its own section, so repeating them in `summary` only "
        "buries the verdict. Longer text fields may use `\\n` escapes to "
        "separate paragraphs, which render as written -- the JSON itself still "
        "has to be one line."
    )


def build_report_blocks(rows: list[tuple[str, dict[str, Any]]]) -> tuple[StageReportBlock, ...]:
    """Turn resolved stage rows into the labelled blocks a prompt renders.

    Preconditions: ``rows`` are ``(stage_id, row)`` pairs as resolved from a
    stage's declaration, already de-duplicated and in declaration order.
    Postconditions: one block per row, carrying whose account it is so an agent
    reading two of them can tell them apart.
    """
    return tuple(
        StageReportBlock(
            stage_id=stage_id,
            stage_name=str(row.get("name") or "") or stage_id,
            # The pass belongs to the report, not to the stage: a stage that
            # ran in three passes has one row and three reports.
            pass_number=_latest_pass(row),
            summary=str(row.get("summary") or ""),
            findings=tuple(item for item in row.get("findings", []) if isinstance(item, str))
            if isinstance(row.get("findings"), list)
            else (),
            validation_evidence=str(row.get("validation_evidence") or ""),
        )
        for stage_id, row in rows
    )


def _latest_pass(row: dict[str, Any]) -> int:
    """Return the pass its newest report was written in, or 0 if it has none."""
    reports = row.get("reports")
    latest = reports[-1] if isinstance(reports, list) and reports else None
    number = latest.get("pass_number") if isinstance(latest, dict) else None
    return number if isinstance(number, int) and not isinstance(number, bool) else 0


def _instructions(stage: LoopStepDefinition) -> str:
    """Return a stage's own instructions; only an agent stage has any."""
    return stage_instructions(stage).strip()


def _open_requests(value: StagePromptInput) -> str:
    """Render what the user is asking of this attempt, as part of its task.

    Carries whatever the caller put on this channel: the stage's open
    feedback when it declares that input, a retry hint, a review gate's
    instruction, or a PR stage's setup. All of it is imperative, which is why
    it sits here and not in the account of what already happened.
    """
    note = value.resolution_note.strip()
    # Labelled, because this zone also holds the stage's own instructions and
    # its context index. Without the label the user's words read as though the
    # loop had always said them.
    return f"\n\nAsked of you for this attempt:\n{note}" if note else ""


def _report_block(report: StageReportBlock) -> str:
    """Render one declared report under a heading naming whose account it is."""
    label = report.stage_name or report.stage_id
    heading = f"Report -- {label}" + (f" (pass {report.pass_number})" if report.pass_number else "")
    findings = "\n".join(f"- {item}" for item in report.findings)
    body = (
        (f"Summary: {report.summary}\n" if report.summary else "")
        + (f"Findings:\n{findings}\n" if findings else "")
        + (
            f"Validation evidence:\n{report.validation_evidence}\n"
            if report.validation_evidence
            else ""
        )
    )
    return f"\n\n{heading}:\n" + (body or "This stage has not reported yet.\n")


def _context_index(value: StagePromptInput) -> str:
    context = value.stage.inputs
    if not context:
        return ""
    if value.resolved_context:
        lines = [
            f"- {entry}"
            for entry in value.resolved_context
            if not entry.endswith(": resolved when the stage starts")
        ]
        lines.extend(f"- {item}" for item in value.context_warnings)
        return "\n\nResolved context index:\n" + "\n".join(lines) if lines else ""
    lines = []
    for item in context:
        detail = ", ".join(item.paths) or item.step or item.ref or "backend-resolved"
        requirement = "required" if item.required else "optional"
        lines.append(f"- {item.kind.value} ({requirement}): {detail}")
    return "\n\nContext references:\n" + "\n".join(lines)


def _brief(value: StagePromptInput) -> str:
    """Render Work-owned input separately from immutable template instructions."""
    if not value.brief_note.strip() and not value.brief_context:
        return ""
    lines = []
    if value.brief_note.strip():
        lines.append(value.brief_note.strip())
    lines.extend(f"- {item}" for item in value.brief_context)
    return "\n\nFor this work:\n" + "\n".join(lines)


def _report_example() -> str:
    return json.dumps(
        {
            "atelier_loop_step_report": {
                "outcome": "pass",
                "summary": "...",
                "criteria": [{"text": "Acceptance criterion", "met": True, "note": ""}],
                "findings": [{"severity": "medium", "text": "...", "location": "file.py:10"}],
                "changed_files": [{"path": "file.py", "additions": 4, "deletions": 1}],
                "changes": "...",
                "validation_evidence": "...",
                "divergences": "None.",
                "skipped_scope": "None.",
                "blocker": "None.",
                "artifact_refs": [],
            }
        },
        separators=(",", ":"),
    )


def stage_report_repair_prompt(stage: LoopStepDefinition) -> str:
    """Request a corrected structured report without repeating stage context."""
    return (
        f"Your latest response did not include a valid report for stage "
        f"`{stage.step_id}`. Finish any remaining stage work, then respond with "
        "exactly one single-line JSON report using this shape and no Markdown "
        f"fence:\n{_report_example()}"
    )


def stage_inactivity_recovery_prompt() -> str:
    """Nudge a stalled stage agent that still holds its own session.

    The nudge lands in the transcript that already carries the stage request,
    so restating it would read as a new instruction and invite the agent to
    redo settled work.
    """
    return "continue"


__all__ = [
    "PrStagePrompt",
    "ReviewStagePrompt",
    "StagePromptInput",
    "TaskStagePrompt",
    "build_follow_up_prompt",
    "build_stage_prompt",
    "prompt_for",
    "stage_inactivity_recovery_prompt",
    "stage_report_repair_prompt",
]
