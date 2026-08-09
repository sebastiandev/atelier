"""Pure state actions shared by every loop target."""

from __future__ import annotations

import re
import shlex
from datetime import UTC, datetime
from typing import Any

from src.domain.loop.dtos import (
    PREVIOUS_STAGE,
    LoopChangedFile,
    LoopDefinition,
    LoopRunStatus,
    LoopStatus,
    LoopStepDefinition,
    LoopStepStatus,
)
from src.domain.loop.snapshots import definition_snapshot
from src.domain.loop.stage_access import stage_agent_policy

_VENV_ACTIVATION = re.compile(
    r"^\s*(?:source|\.)\s+['\"]?[^;&|\n]*\.venv/bin/activate['\"]?\s*(?:&&|\n)\s*"
)
_SHELL_OPERATORS = {";", "&&", "||", "|", "&"}


def sourced_worktree_slug(source_ref: str) -> str:
    """Return the worktree slug shared by every run of one source.

    Preconditions: ``source_ref`` identifies the run's source (today, a plan
    artifact id).
    Postconditions: the slug is stable, so a later run of the same source
    attaches to the existing worktree instead of forking a new one.

    A sourceless run uses the constant ``"loop"`` slug for the same reason.
    Story runs used to default to the launching agent's slug, giving each run
    its own worktree and its own branch -- which silently breaks the promise
    that a follow-up "keeps the same branch; an open pull request is updated
    in place".
    """
    return f"loop-{source_ref}"


def initialized_loop_snapshot(
    *,
    target_id: str,
    agent_slug: str,
    run_id: str,
    definition: LoopDefinition,
    entry_step_id: str | None = None,
    entry_agent_slug: str | None = None,
    source_agent_slug: str | None = None,
    entry_is_agent: bool = True,
) -> dict[str, Any]:
    """Build initial state for one revision-pinned loop run.

    Preconditions: the definition is valid and has at least one stage.
    Postconditions: stages before the entry are skipped, the entry is active,
    and later stages are pending.
    """
    entry_id = entry_step_id or definition.stages[0].step_id
    entry_index = next(
        (index for index, stage in enumerate(definition.stages) if stage.step_id == entry_id),
        -1,
    )
    if entry_index < 0:
        raise ValueError(f"loop entry stage not found: {entry_id}")
    entry = definition.stages[entry_index]
    return {
        "loop_run_id": loop_run_id(target_id, agent_slug, run_id=run_id),
        "definition_id": definition.definition_id,
        "definition_name": definition.name,
        "definition_revision": definition.revision,
        "definition_snapshot": definition_snapshot(definition),
        "status": LoopStatus.RUNNING.value,
        "status_reason": f"{entry.name} is running.",
        "attempt": 1,
        "pass_number": 1,
        "latest_report_id": None,
        "latest_assessment_id": None,
        "findings": [],
        "last_checked_seq": 0,
        "current_stage_id": entry.step_id,
        "source_agent_slug": source_agent_slug or agent_slug,
        "owned_agent_slugs": [agent_slug],
        "stages": [
            {
                "id": stage.step_id,
                "name": stage.name,
                "kind": stage.kind.value,
                "status": (
                    LoopStepStatus.SKIPPED.value
                    if index < entry_index
                    else LoopStepStatus.RUNNING.value
                    if index == entry_index
                    else LoopStepStatus.PENDING.value
                ),
                "attempt": 1 if index == entry_index else 0,
                "max_attempts": stage.retry.max_attempts,
                "agent_slug": (
                    (entry_agent_slug or agent_slug)
                    if index == entry_index and entry_is_agent
                    else None
                ),
                "permissions": _permissions_value(stage),
                "session": _session_value(stage),
                "summary": "",
                "findings": [],
            }
            for index, stage in enumerate(definition.stages)
        ],
    }


def loop_run_id(target_id: str, agent_slug: str, *, run_id: str | None = None) -> str:
    """Return a stable storage key for one loop run.

    Preconditions: target and agent identifiers are non-empty.
    Postconditions: the result is safe for persisted identifiers and paths.
    """
    raw_suffix = run_id or agent_slug
    safe_suffix = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_suffix).strip("-").lower()
    return f"loop-{target_id}-{safe_suffix}"


def stage_row(loop: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    """Return one mutable stage row from a loop snapshot."""
    rows = loop.get("stages")
    if not isinstance(rows, list):
        return None
    return next(
        (row for row in rows if isinstance(row, dict) and row.get("id") == step_id),
        None,
    )


def declared_report_rows(
    loop: dict[str, Any], stage: Any, last_reported_id: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    """Resolve the stage rows a stage declares that it reads, in order.

    A stage may name several: a reviewer judging whether a correction answered
    the original finding needs both accounts, and the single ``previous_report``
    context could only ever carry one.

    Preconditions: ``stage`` is a stage definition with a ``reports`` sequence;
    ``last_reported_id`` is the stage that reported into this one, used to
    resolve the symbolic ``previous``.
    Postconditions: one ``(stage_id, row)`` pair per resolvable declaration,
    de-duplicated by stage id so a ``previous`` that resolves to a stage also
    named explicitly renders once. Declarations naming a stage with no row yet
    are skipped, which is how an optional report reads as absent.
    """
    resolved: dict[str, dict[str, Any]] = {}
    fallback = last_reported_id or _latest_reporting_stage(loop, stage)
    for reference in getattr(stage, "reports", ()):
        step_id = reference.from_stage
        if step_id == PREVIOUS_STAGE:
            step_id = fallback
        if not step_id or step_id in resolved:
            continue
        row = stage_row(loop, step_id)
        if row is not None:
            resolved[step_id] = row
    return list(resolved.items())


def unresolved_report_warnings(
    loop: dict[str, Any], stage: Any, last_reported_id: str = ""
) -> list[str]:
    """Return a note for each required report the run cannot supply.

    A stage cannot conjure an account of work that was never reported, and
    neither can the user, so this never blocks -- it says plainly that the
    input is absent rather than leaving a hole where the agent expects a
    report and letting it guess what belonged there.

    Preconditions: as :func:`declared_report_rows`.
    Postconditions: one line per required declaration that resolved to
    nothing; optional declarations are silent, which is what optional means.
    """
    resolved = {step_id for step_id, _ in declared_report_rows(loop, stage, last_reported_id)}
    fallback = last_reported_id or _latest_reporting_stage(loop, stage)
    notes = []
    for reference in getattr(stage, "reports", ()):
        if not reference.required:
            continue
        step_id = fallback if reference.from_stage == PREVIOUS_STAGE else reference.from_stage
        if step_id and step_id in resolved:
            continue
        named = step_id or "the preceding stage"
        notes.append(f"required report from {named}: never reported")
    return notes


def _latest_reporting_stage(loop: dict[str, Any], stage: Any) -> str:
    """Return the stage that reported most recently, other than this one.

    Only used when the run has no recorded referent for ``previous`` -- a run
    pinned before the run started recording one, or a transition that moved the
    current stage without going through a stage report. Falling back to the
    newest report keeps such a run readable instead of rendering no account at
    all, which is what the old single-report path always managed to do.
    """
    rows = loop.get("stages")
    current = getattr(stage, "step_id", "")
    latest_seq = -1
    latest_id = ""
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("id") == current:
            continue
        reports = row.get("reports")
        newest = reports[-1] if isinstance(reports, list) and reports else None
        seq = int_or_default(newest.get("seq"), 0) if isinstance(newest, dict) else -1
        if seq > latest_seq:
            latest_seq = seq
            latest_id = str_or_empty(row.get("id"))
    return latest_id


def start_next_pass(loop: dict[str, Any]) -> int:
    """Advance and persist the loop's pass counter.

    Preconditions: ``loop`` is a mutable loop-run snapshot.
    Postconditions: ``pass_number`` is one greater and the new value is returned.
    """
    pass_number = max(1, int_or_default(loop.get("pass_number"), 1)) + 1
    loop["pass_number"] = pass_number
    return pass_number


def changed_file_prompt_lines(value: object) -> tuple[str, ...]:
    """Return compact changed-file report lines from DTO or persisted rows."""
    if not isinstance(value, (list, tuple)):
        return ()
    rows: list[str] = []
    for item in value:
        if isinstance(item, LoopChangedFile):
            path, additions, deletions = item.path, item.additions, item.deletions
        elif isinstance(item, dict):
            path = str_or_empty(item.get("path"))
            additions = int_or_default(item.get("additions"), 0)
            deletions = int_or_default(item.get("deletions"), 0)
        else:
            continue
        if path:
            rows.append(f"{path} (+{additions}/-{deletions})")
    return tuple(rows)


def latest_changed_file_prompt_lines(loop: dict[str, Any]) -> tuple[str, ...]:
    """Return the chronologically latest non-empty changed-file report."""
    stages = loop.get("stages")
    if not isinstance(stages, list):
        return ()
    latest: tuple[str, int, tuple[str, ...]] | None = None
    order = 0
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("reports"), list):
            continue
        for report in stage["reports"]:
            order += 1
            if not isinstance(report, dict):
                continue
            lines = changed_file_prompt_lines(report.get("changed_files"))
            if not lines:
                continue
            candidate = (
                str_or_empty(report.get("recorded_at")),
                order,
                lines,
            )
            if latest is None or candidate[:2] > latest[:2]:
                latest = candidate
    if latest is not None:
        return latest[2]
    for stage in reversed(stages):
        if not isinstance(stage, dict):
            continue
        lines = changed_file_prompt_lines(stage.get("changed_files"))
        if lines:
            return lines
    return ()


def run_agent_slugs(run: dict[str, Any], loop: dict[str, Any]) -> tuple[str, ...]:
    """Return each run-owned agent once in launch order."""
    slugs: list[str] = []
    owned = loop.get("owned_agent_slugs")
    if isinstance(owned, list):
        slugs.extend(slug for slug in owned if isinstance(slug, str) and slug)
    initial = str_or_none(run.get("agent_slug"))
    if initial and initial not in slugs:
        slugs.append(initial)
    stages = loop.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            slug = str_or_none(stage.get("agent_slug"))
            if slug and slug not in slugs:
                slugs.append(slug)
    return tuple(slugs)


def run_status(run: dict[str, Any]) -> LoopRunStatus:
    """Return a normalized aggregate status from persisted run state."""
    value = run.get("status")
    try:
        return LoopRunStatus(value) if isinstance(value, str) else LoopRunStatus.RUNNING
    except ValueError:
        return LoopRunStatus.RUNNING


def claim_connection_recovery(stage: dict[str, Any], error: str) -> bool:
    """Claim one automatic transport recovery for the current stage attempt.

    Preconditions: ``stage`` is the persisted active-stage row.
    Postconditions: a connection-close error is marked once for this attempt;
    unrelated errors and repeated closes return false without changing state.
    """
    if "connection closed" not in error.casefold():
        return False
    attempt = int_or_default(stage.get("attempt"), 1)
    if int_or_default(stage.get("connection_recovered_attempt"), 0) == attempt:
        return False
    stage["connection_recovered_attempt"] = attempt
    return True


def claim_stale_permission_recovery(stage: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """Claim one continuation for newly expired permission requests.

    Preconditions: events belong to the active stage agent and are ordered.
    Postconditions: synthetic stale denials are marked once; user denials and
    previously recovered requests leave the stage unchanged.
    """
    recovered = set(str_list(stage.get("recovered_stale_permission_ids")))
    stale_ids = {
        request_id
        for event in events
        if event.get("type") == "permission_decision"
        and event.get("decision") == "deny"
        and event.get("stale") is True
        and isinstance((request_id := event.get("request_id")), str)
    }
    if stale_ids <= recovered:
        return False
    stage["recovered_stale_permission_ids"] = sorted(recovered | stale_ids)
    return True


def command_matches_approved_prefix(
    command: str,
    approved_prefixes: tuple[str, ...],
) -> bool:
    """Return whether one simple shell command matches an approved token prefix.

    Preconditions: prefixes are validated, single-line loop configuration.
    Postconditions: virtualenv activation is ignored; compound shell commands
    and malformed quoting are never approved automatically.
    """
    command_tokens = _simple_command_tokens(command)
    if not command_tokens:
        return False
    return any(
        prefix_tokens and command_tokens[: len(prefix_tokens)] == prefix_tokens
        for prefix in approved_prefixes
        if (prefix_tokens := _simple_command_tokens(prefix))
    )


def permission_request_matches_approved_prefix(
    event: dict[str, Any],
    approved_prefixes: tuple[str, ...],
) -> bool:
    """Return whether a provider permission event is an approved command.

    Preconditions: ``event`` is one canonical transcript entry.
    Postconditions: only command-bearing permission requests can match.
    """
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return (
        event.get("type") == "permission_request"
        and isinstance(command, str)
        and command_matches_approved_prefix(command, approved_prefixes)
    )


def approved_command_prefix_from_request(event: dict[str, Any]) -> str | None:
    """Return a conservative reusable prefix from one shell permission request.

    Preconditions: ``event`` is a canonical transcript entry. Postconditions:
    simple commands yield at most their first two shell tokens; compound or
    non-command requests yield ``None``.
    """
    if event.get("type") != "permission_request":
        return None
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return None
    tokens = _simple_command_tokens(command)
    return shlex.join(tokens[:2]) if tokens else None


def _simple_command_tokens(command: str) -> tuple[str, ...]:
    normalized = _VENV_ACTIVATION.sub("", command.strip(), count=1)
    if not normalized or "\n" in normalized or "\r" in normalized:
        return ()
    try:
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = tuple(lexer)
    except ValueError:
        return ()
    return () if any(token in _SHELL_OPERATORS for token in tokens) else tokens


def loop_status(value: object, fallback: LoopRunStatus) -> LoopStatus:
    """Return a persisted loop status or infer one from aggregate run state."""
    try:
        return LoopStatus(value) if isinstance(value, str) else _loop_status_fallback(fallback)
    except ValueError:
        return _loop_status_fallback(fallback)


def _loop_status_fallback(status: LoopRunStatus) -> LoopStatus:
    """Infer a displayable loop status from aggregate run state."""
    if status == LoopRunStatus.RUNNING:
        return LoopStatus.RUNNING
    if status == LoopRunStatus.NEEDS_ATTENTION:
        return LoopStatus.NEEDS_AGENT
    if status == LoopRunStatus.BLOCKED:
        return LoopStatus.BLOCKED_USER
    if status == LoopRunStatus.WAITING_APPROVAL:
        return LoopStatus.AWAITING_APPROVAL
    if status == LoopRunStatus.ACCEPTED:
        return LoopStatus.ACCEPTED
    return LoopStatus.COMPLETED


def now_iso() -> str:
    """Return the canonical loop timestamp string."""
    return datetime.now(UTC).isoformat()


def dict_or_empty(value: object) -> dict[str, Any]:
    """Return a mutable mapping value or an empty mapping."""
    return value if isinstance(value, dict) else {}


def str_or_none(value: object) -> str | None:
    """Return a non-empty string value when present."""
    return value if isinstance(value, str) and value else None


def str_or_empty(value: object) -> str:
    """Return a string value or the empty string."""
    return value if isinstance(value, str) else ""


def int_or_default(value: object, default: int) -> int:
    """Return an integer value or a caller-supplied default."""
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def str_list(value: object) -> list[str]:
    """Return only string members from a persisted list."""
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []



def _permissions_value(stage: LoopStepDefinition) -> str | None:
    """The stage's declared permission, or ``None`` when it launches no agent."""
    policy = stage_agent_policy(stage)
    if policy is None or policy.permissions is None:
        return None
    return policy.permissions.value


def _session_value(stage: LoopStepDefinition) -> str | None:
    """The stage's session policy, or ``None`` when it launches no agent."""
    policy = stage_agent_policy(stage)
    return policy.session.value if policy is not None else None


__all__ = [
    "approved_command_prefix_from_request",
    "changed_file_prompt_lines",
    "claim_connection_recovery",
    "claim_stale_permission_recovery",
    "command_matches_approved_prefix",
    "declared_report_rows",
    "dict_or_empty",
    "initialized_loop_snapshot",
    "int_or_default",
    "latest_changed_file_prompt_lines",
    "loop_run_id",
    "loop_status",
    "now_iso",
    "permission_request_matches_approved_prefix",
    "run_agent_slugs",
    "run_status",
    "sourced_worktree_slug",
    "stage_row",
    "start_next_pass",
    "str_list",
    "str_or_empty",
    "str_or_none",
    "unresolved_report_warnings",
]
