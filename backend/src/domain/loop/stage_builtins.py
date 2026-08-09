"""Built-in reusable stage definitions shipped with Atelier."""

from __future__ import annotations

from src.domain.loop.dtos import (
    ApprovalStage,
    CheckStage,
    LoopAgentPolicy,
    LoopContextKind,
    LoopContextReference,
    LoopOutcome,
    LoopPermission,
    LoopPrConfig,
    LoopReportReference,
    LoopRetryPolicy,
    LoopReviewGate,
    LoopSessionPolicy,
    LoopStepDefinition,
    PrStage,
    ReviewStage,
    StageDefinition,
    StageDefinitionScope,
    TaskStage,
)
from src.domain.loop.pr_lifecycle import CREATE_PR_STAGE_INSTRUCTIONS
from src.domain.loop.stages import prepare_stage_definition


def builtin_stage_definitions() -> tuple[StageDefinition, ...]:
    """Return validated immutable stages bundled with Atelier."""
    return tuple(
        prepare_stage_definition(definition)
        for definition in (
            _implement(),
            _validate(),
            _review(
                "code-review",
                "Code review",
                "Review the implementation diff for correctness, security, "
                "maintainability, and acceptance-criteria gaps.",
            ),
            _review(
                "security-review",
                "Security review",
                "Review the workspace diff for security risks, cite each affected "
                "location, and assign severity.",
            ),
            _approve(),
            _create_pr(),
        )
    )


def builtin_stage_definition(definition_id: str) -> StageDefinition | None:
    """Return one built-in stage by stable id."""
    return next(
        (item for item in builtin_stage_definitions() if item.definition_id == definition_id),
        None,
    )


def _definition(
    definition_id: str,
    name: str,
    description: str,
    stage: LoopStepDefinition,
    outcomes: tuple[LoopOutcome, ...],
) -> StageDefinition:
    return StageDefinition(
        definition_id=definition_id,
        name=name,
        description=description,
        scope=StageDefinitionScope.BUILTIN,
        stage=stage,
        outcomes=outcomes,
    )


def _implement() -> StageDefinition:
    return _definition(
        "implement",
        "Implement",
        "Implement the goal in the shared Work workspace.",
        TaskStage(
            step_id="implement",
            name="Implement",
            instructions=(
                "Implement the target end to end. Follow repository guidance, "
                "update tests, and report changed files and validation evidence."
            ),
            inputs=(LoopContextReference(LoopContextKind.TARGET, required=True),),
            agent=LoopAgentPolicy(
                session=LoopSessionPolicy.FRESH, permissions=LoopPermission.WRITE
            ),
                retry=LoopRetryPolicy(max_attempts=3, timeout_minutes=45),
            note_required=False,
        ),
        (LoopOutcome.PASS, LoopOutcome.BLOCKED_USER, LoopOutcome.FAILED),
    )


def _validate() -> StageDefinition:
    return _definition(
        "validate",
        "Validate",
        "Run the repository test command as a deterministic check.",
        CheckStage(
            step_id="validate",
            name="Validate",
            check_adapter="command",
            check_command=("pytest",),
            retry=LoopRetryPolicy(max_attempts=1, timeout_minutes=20),
        ),
        (LoopOutcome.PASS, LoopOutcome.CHANGES_REQUESTED, LoopOutcome.FAILED),
    )


def _review(definition_id: str, name: str, instructions: str) -> StageDefinition:
    return _definition(
        definition_id,
        name,
        instructions,
        ReviewStage(
            step_id=definition_id,
            name=name,
            instructions=instructions,
            inputs=(
                LoopContextReference(LoopContextKind.TARGET, required=True),
                LoopContextReference(LoopContextKind.WORKSPACE_DIFF, required=True),
            ),
            agent=LoopAgentPolicy(session=LoopSessionPolicy.FRESH, permissions=LoopPermission.READ),
                retry=LoopRetryPolicy(max_attempts=2, timeout_minutes=15),
            note_required=False,
            review_gate=LoopReviewGate(),
        ),
        (
            LoopOutcome.PASS,
            LoopOutcome.CHANGES_REQUESTED,
            LoopOutcome.BLOCKED_USER,
            LoopOutcome.FAILED,
        ),
    )


def _approve() -> StageDefinition:
    return _definition(
        "approve",
        "Approve",
        "Hold the result for an explicit human decision.",
        ApprovalStage(
            step_id="approve",
            name="Approve",
            retry=LoopRetryPolicy(max_attempts=1, timeout_minutes=1),
        ),
        (LoopOutcome.PASS, LoopOutcome.CHANGES_REQUESTED),
    )


def _create_pr() -> StageDefinition:
    return _definition(
        "create-pr",
        "Create PR",
        "Commit, push, and create or update the Work pull request.",
        PrStage(
            step_id="create-pr",
            name="Create PR",
            instructions=CREATE_PR_STAGE_INSTRUCTIONS,
            inputs=(
                LoopContextReference(LoopContextKind.WORKSPACE_DIFF, required=True),
                LoopContextReference(LoopContextKind.CHANGED_FILES, required=True),
            ),
            reports=(LoopReportReference(),),
            agent=LoopAgentPolicy(
                session=LoopSessionPolicy.FRESH,
                permissions=LoopPermission.WRITE,
                approved_command_prefixes=("git add", "git commit"),
            ),
                retry=LoopRetryPolicy(max_attempts=2, timeout_minutes=20),
            pr_config=LoopPrConfig(),
        ),
        (LoopOutcome.PASS, LoopOutcome.BLOCKED_USER, LoopOutcome.FAILED),
    )


__all__ = ["builtin_stage_definition", "builtin_stage_definitions"]
