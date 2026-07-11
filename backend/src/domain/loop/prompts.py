"""Backend-owned provider prompts for multi-stage loop execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import singledispatch

from src.domain.loop.dtos import LoopStepDefinition


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
    resolution_note: str = ""
    resolved_context: tuple[str, ...] = ()
    context_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskStagePrompt(StagePromptInput):
    """Prompt input for an implementation or corrective write stage."""


@dataclass(frozen=True)
class ReviewStagePrompt(StagePromptInput):
    """Prompt input for an independent read-only review stage."""


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
            "You are an independent read-only reviewer. Inspect the workspace and "
            "diff, do not modify files, and request changes for any material gap."
        ),
    )


def _shared_prompt(value: StagePromptInput, *, posture: str) -> str:
    previous = ""
    if value.previous_summary or value.previous_findings:
        findings = "\n".join(f"- {item}" for item in value.previous_findings)
        previous = (
            "\n\nPrevious stage report:\n"
            + (f"Summary: {value.previous_summary}\n" if value.previous_summary else "")
            + (f"Findings:\n{findings}\n" if findings else "")
        )
    resolution = (
        f"\n\nUser resolution:\n{value.resolution_note.strip()}"
        if value.resolution_note.strip()
        else ""
    )
    return (
        f"Execute loop run `{value.run_id}`, stage `{value.stage.step_id}` "
        f"({value.stage.name}) for `{value.artifact_id}`: {value.artifact_title}.\n\n"
        f"Source artifact: {value.source_ref}\n"
        f"Work: {value.work_slug}\n\n"
        f"{posture}\n\n"
        f"Stage instructions:\n{value.stage.instructions.strip()}"
        f"{_context_index(value)}"
        f"{previous}{resolution}\n\n"
        "When this stage reaches a stopping point, respond with exactly one "
        "single-line JSON report and no Markdown fence:\n"
        f"{_report_example()}\n\n"
        "Use outcome `blocked_user` only for a concrete decision or action that "
        "only the user can provide. Use `changes_requested` only from a review "
        "stage. Use explicit `None.` strings when a text field has no content."
    )


def _context_index(value: StagePromptInput) -> str:
    context = value.stage.context
    if not context:
        return ""
    if value.resolved_context:
        lines = [f"- {entry}" for entry in value.resolved_context]
        lines.extend(f"- optional missing: {item}" for item in value.context_warnings)
        return "\n\nResolved context index:\n" + "\n".join(lines)
    lines = []
    for item in context:
        detail = ", ".join(item.paths) or item.step or item.ref or "backend-resolved"
        requirement = "required" if item.required else "optional"
        lines.append(f"- {item.kind.value} ({requirement}): {detail}")
    return "\n\nContext references:\n" + "\n".join(lines)


def _report_example() -> str:
    return json.dumps(
        {
            "atelier_loop_step_report": {
                "outcome": "pass",
                "summary": "...",
                "criteria": [
                    {"text": "Acceptance criterion", "met": True, "note": ""}
                ],
                "findings": [
                    {"severity": "medium", "text": "...", "location": "file.py:10"}
                ],
                "changed_files": [
                    {"path": "file.py", "additions": 4, "deletions": 1}
                ],
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


__all__ = [
    "ReviewStagePrompt",
    "StagePromptInput",
    "TaskStagePrompt",
    "build_stage_prompt",
    "stage_report_repair_prompt",
]
