"""Built-in reusable loop definitions shipped with Atelier."""

from __future__ import annotations

from src.domain.loop.definitions import prepare_definition
from src.domain.loop.dtos import (
    LoopAgentPolicy,
    LoopContextKind,
    LoopContextReference,
    LoopDefinition,
    LoopDefinitionScope,
    LoopHistoryLevel,
    LoopOutcome,
    LoopPermission,
    LoopReportField,
    LoopReportReference,
    LoopReportSchema,
    LoopRetryPolicy,
    LoopReviewGate,
    LoopSessionPolicy,
    LoopStepDefinition,
    LoopStepKind,
)

_GENERIC_REPORT = LoopReportSchema(
    schema_id="multi-stage-loop-report",
    fields=(
        LoopReportField("summary", "Summary", allow_explicit_none=False),
        LoopReportField("findings", "Findings"),
        LoopReportField("changes", "Changes"),
        LoopReportField("validation_evidence", "Validation evidence", allow_explicit_none=False),
        LoopReportField("divergences", "Divergences"),
        LoopReportField("skipped_scope", "Skipped scope"),
        LoopReportField("blocker", "Blocker"),
    ),
)

_IMPLEMENTATION = """Implement the target artifact end to end.

- Work only inside the assigned run workspace.
- Follow the acceptance criteria exactly; do not expand scope.
- If you must diverge, stop and report the divergence rather than guessing.
- Add or update tests that fail without your change.
- Report changed files, validation evidence, and any skipped scope.
"""

_CODE_REVIEW = """Review the implementation diff against the artifact and its acceptance criteria.

You are a fresh, read-only reviewer and cannot modify the workspace.

- Confirm each acceptance criterion is met, or list the gap.
- Flag correctness, security, and maintainability issues with a severity.
- Reference the file and line for every finding.
- Return pass only when the diff is ready for human approval; otherwise return
  changes requested with actionable findings.
"""

_SECURITY_REVIEW = """Perform an independent security review of the run workspace and diff.

Read-only. No network access is granted by this stage.

- Inspect the diff, reports, and repository policies provided as context.
- Check auth, session handling, input validation, secrets, and data exposure.
- Assign a severity to every finding and cite the location.
- Return changes requested for any high or critical finding.
"""


def builtin_loop_definitions() -> tuple[LoopDefinition, ...]:
    """Return validated immutable definitions bundled with Atelier.

    Preconditions: built-in constants are importable.
    Postconditions: every returned definition contains its content revision.
    """
    return tuple(prepare_definition(definition) for definition in (_fast(), _reviewed(), _secure()))


def builtin_loop_definition(definition_id: str) -> LoopDefinition | None:
    """Return one built-in definition by stable id."""
    return next(
        (
            definition
            for definition in builtin_loop_definitions()
            if definition.definition_id == definition_id
        ),
        None,
    )


def _fast() -> LoopDefinition:
    return _definition(
        "atelier-fast",
        "Atelier Fast",
        "Direct implementation with one human approval for low-risk work.",
        (
            _implementation("approval"),
            _approval(),
        ),
    )


def _reviewed() -> LoopDefinition:
    return _definition(
        "atelier-reviewed",
        "Atelier Reviewed",
        "Implementation, independent code review, then human approval.",
        (
            _implementation("code-review"),
            _review("code-review", "Code review", _CODE_REVIEW, "approval"),
            _approval(),
        ),
        is_default=True,
    )


def _secure() -> LoopDefinition:
    return _definition(
        "atelier-secure",
        "Atelier Secure",
        "Implementation, code review, security review, then human approval.",
        (
            _implementation("code-review"),
            _review("code-review", "Code review", _CODE_REVIEW, "security-review"),
            _review(
                "security-review",
                "Security review",
                _SECURITY_REVIEW,
                "approval",
                policy_paths=("docs/policies/*.md",),
            ),
            _approval(),
        ),
    )


def _definition(
    definition_id: str,
    name: str,
    description: str,
    stages: tuple[LoopStepDefinition, ...],
    *,
    is_default: bool = False,
) -> LoopDefinition:
    return LoopDefinition(
        definition_id=definition_id,
        name=name,
        trigger="artifact_or_objective",
        report_schema=_GENERIC_REPORT,
        retry_limit=3,
        description=description,
        scope=LoopDefinitionScope.BUILTIN,
        is_default=is_default,
        stages=stages,
    )


def _implementation(pass_to: str) -> LoopStepDefinition:
    return LoopStepDefinition(
        step_id="implementation",
        name="Implementation",
        kind=LoopStepKind.AGENT_TASK,
        instructions=_IMPLEMENTATION,
        inputs=(
            LoopContextReference(LoopContextKind.TARGET, required=True),
            LoopContextReference(LoopContextKind.PLAN_INDEX),
            LoopContextReference(LoopContextKind.FEEDBACK),
        ),
        # The stage a review sends back to has to be shown what it was sent
        # back for. This used to arrive as an injected corrective note; it is
        # a report, and now it is declared as one.
        reports=(LoopReportReference(),),
        # A fresh agent every pass, so without this it cannot tell a first
        # attempt from a fourth and retries what has already failed twice.
        history=LoopHistoryLevel.SUMMARIES,
        agent=LoopAgentPolicy(
            session=LoopSessionPolicy.FRESH,
            permissions=LoopPermission.WRITE,
        ),
        report_contract="implementation",
        note_required=False,
        retry=LoopRetryPolicy(max_attempts=3, timeout_minutes=45),
        transitions={
            LoopOutcome.PASS: pass_to,
            LoopOutcome.BLOCKED_USER: "pause",
            LoopOutcome.FAILED: "fail",
        },
    )


#: Read-only commands a reviewer may run without an approval prompt. A
#: chained command is approved only when *every* segment matches one of these
#: (see domain/agents/command_approval.py), which is why the pipe-friendly
#: filters are listed alongside the searches.
REVIEW_COMMAND_PREFIXES: tuple[str, ...] = (
    "git diff",
    "git status",
    "rg",
    "sed",
    "grep",
    "sort",
    "uniq",
    "wc",
    "tail",
    "cut",
    "ls",
    "head",
    "echo",
    "cd",
)


def _review(
    step_id: str,
    name: str,
    instructions: str,
    pass_to: str,
    *,
    policy_paths: tuple[str, ...] = ("docs/adr/*.md", "docs/architecture.md"),
) -> LoopStepDefinition:
    return LoopStepDefinition(
        step_id=step_id,
        name=name,
        kind=LoopStepKind.AGENT_REVIEW,
        instructions=instructions,
        inputs=(
            LoopContextReference(LoopContextKind.TARGET, required=True),
            LoopContextReference(LoopContextKind.WORKSPACE_DIFF, required=True),
            LoopContextReference(LoopContextKind.WAIVED_FINDINGS),
            LoopContextReference(LoopContextKind.FEEDBACK),
            LoopContextReference(LoopContextKind.FILES, paths=policy_paths),
        ),
        # Named rather than symbolic: a trailing corrective stage must not mask
        # the substantive account the review is configured to read.
        reports=(LoopReportReference("implementation"),),
        history=LoopHistoryLevel.SUMMARIES,
        agent=LoopAgentPolicy(
            session=LoopSessionPolicy.FRESH,
            permissions=LoopPermission.READ,
            # A read stage asks before every command, so inspection stalls on
            # approval prompts. These are the read-only tools a reviewer needs
            # to navigate a diff; prefixes are matched token-wise, so `rg`
            # covers any `rg ...`. Nothing here can write.
            approved_command_prefixes=REVIEW_COMMAND_PREFIXES,
        ),
        report_contract="review",
        review_gate=LoopReviewGate(),
        # The reviewer already gets the goal, the target and the workspace
        # diff, so it has what it needs without a note. A definition author can
        # still declare one; the built-ins just don't block Start on it.
        note_required=False,
        retry=LoopRetryPolicy(max_attempts=2, timeout_minutes=15),
        transitions={
            LoopOutcome.PASS: pass_to,
            LoopOutcome.CHANGES_REQUESTED: "implementation",
            LoopOutcome.BLOCKED_USER: "pause",
            LoopOutcome.FAILED: "fail",
        },
    )


def _approval() -> LoopStepDefinition:
    return LoopStepDefinition(
        step_id="approval",
        name="Approve result",
        kind=LoopStepKind.USER_APPROVAL,
        retry=LoopRetryPolicy(max_attempts=1, timeout_minutes=1),
        transitions={LoopOutcome.CHANGES_REQUESTED: "implementation"},
    )


__all__ = ["builtin_loop_definition", "builtin_loop_definitions"]
