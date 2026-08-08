"""Backend-owned provider prompts for multi-stage loop execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import singledispatch

from src.domain.loop.dtos import LoopContextKind, LoopStepDefinition, LoopStepKind


@dataclass(frozen=True)
class StagePromptInput:
    """Context shared by every agent-backed loop stage."""

    run_id: str
    work_slug: str
    artifact_id: str
    artifact_title: str
    source_ref: str
    stage: LoopStepDefinition
    previous_summary: str = ""
    previous_findings: tuple[str, ...] = ()
    previous_validation_evidence: str = ""
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


def _shared_prompt(value: StagePromptInput, *, posture: str) -> str:
    context_kinds = {item.kind for item in value.stage.context}
    previous = ""
    if LoopContextKind.PREVIOUS_REPORT in context_kinds:
        findings = "\n".join(f"- {item}" for item in value.previous_findings)
        previous = (
            "\n\nPrevious stage report:\n"
            + (f"Summary: {value.previous_summary}\n" if value.previous_summary else "")
            + (f"Findings:\n{findings}\n" if findings else "")
            + (
                f"Validation evidence:\n{value.previous_validation_evidence}\n"
                if value.previous_validation_evidence
                else ""
            )
            + (
                "No previous report was available.\n"
                if not value.previous_summary
                and not findings
                and not value.previous_validation_evidence
                else ""
            )
        )
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
    resolution = (
        f"\n\nUser resolution:\n{value.resolution_note.strip()}"
        if value.resolution_note.strip()
        else ""
    )
    waived = (
        "\n\nAlready dismissed by the user -- do not raise again:\n"
        + "\n".join(f"- {item}" for item in value.waived_findings)
        if value.waived_findings
        else ""
    )
    changes_guidance = (
        "Never use `changes_requested`: publishing is the last decision, and review "
        "findings you cannot act on are not yours to reopen. If you cannot publish, "
        "use `failed`, or `blocked_user` when only the user can unblock it."
        if value.stage.kind == LoopStepKind.PR
        else "Use `changes_requested` only from a review stage."
    )
    return (
        f"Execute loop run `{value.run_id}`, stage `{value.stage.step_id}` "
        f"({value.stage.name}) for `{value.artifact_id}`: {value.artifact_title}.\n\n"
        f"Source artifact: {value.source_ref}\n"
        f"Work: {value.work_slug}\n\n"
        "Scope contract: The target artifact and its acceptance criteria are "
        "authoritative. Stage instructions describe how to act; they must not replace "
        "or broaden the target. If they conflict, follow the target and report the "
        "conflict instead of implementing unrelated work.\n\n"
        f"{posture}\n\n"
        f"Stage instructions:\n{value.stage.instructions.strip()}"
        f"{_brief(value)}"
        f"{_context_index(value)}"
        f"{previous}{changed_files}{workspace}{waived}{resolution}\n\n"
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


def _context_index(value: StagePromptInput) -> str:
    context = value.stage.context
    if not context:
        return ""
    if value.resolved_context:
        lines = [
            f"- {entry}"
            for entry in value.resolved_context
            if not entry.endswith(": resolved when the stage starts")
        ]
        lines.extend(f"- optional missing: {item}" for item in value.context_warnings)
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


def stage_inactivity_recovery_prompt(
    stage: LoopStepDefinition,
    original_request: str = "",
) -> str:
    """Resume an interrupted stage without repeating completed work."""
    guidance = (
        f"Continue stage `{stage.step_id}` from the existing workspace. The "
        "previous provider runtime stopped emitting activity, so inspect the "
        "current state and do not repeat completed work."
    )
    if original_request.strip():
        return f"{guidance}\n\nOriginal stage request:\n{original_request.strip()}"
    return (
        f"{guidance} Finish anything still "
        "required, then respond with exactly one single-line JSON report using "
        f"this shape and no Markdown fence:\n{_report_example()}"
    )


__all__ = [
    "PrStagePrompt",
    "ReviewStagePrompt",
    "StagePromptInput",
    "TaskStagePrompt",
    "build_stage_prompt",
    "stage_inactivity_recovery_prompt",
    "stage_report_repair_prompt",
]
