"""Tests for configured dynamic Loop prompt context."""

from pathlib import Path
from types import SimpleNamespace

from src.domain.loop import runtime
from src.domain.loop.dtos import (
    LoopContextKind,
    LoopContextReference,
    LoopReportReference,
    LoopStepDefinition,
    LoopStepKind,
)
from src.domain.loop.prompts import (
    ReviewStagePrompt,
    StageReportBlock,
    TaskStagePrompt,
    build_stage_prompt,
)
from src.domain.worktrees import WorktreeState


def _stage(*kinds: LoopContextKind, reports: tuple[str, ...] = ()) -> LoopStepDefinition:
    """Return a review stage with the selected input and report contract."""
    return LoopStepDefinition(
        step_id="review",
        name="Review",
        kind=LoopStepKind.AGENT_REVIEW,
        instructions="Review the implementation.",
        inputs=tuple(LoopContextReference(kind) for kind in kinds),
        reports=tuple(LoopReportReference(from_stage=item) for item in reports),
    )


def _prompt(stage: LoopStepDefinition) -> str:
    """Render one prompt with every available dynamic input."""
    return build_stage_prompt(
        ReviewStagePrompt(
            run_id="run-1",
            work_slug="WRK-001",
            artifact_id="objective",
            artifact_title="Goal",
            source_ref="Goal",
            stage=stage,
            reports=(
                StageReportBlock(
                    stage_id="implementation",
                    stage_name="Implementation",
                    pass_number=4,
                    summary="Implemented the transfer.",
                    findings=("One finding.",),
                    validation_evidence="42 tests passed.",
                ),
            )
            if stage.reports
            else (),
            previous_changed_files=("src/app.py (+4/-1)",),
            workspace_diff="Git: feature/test @ abc123\nStatus:\n M src/app.py",
            resolved_context=(
                "workspace_diff: resolved when the stage starts",
                "previous_report: resolved when the stage starts",
            ),
        )
    )


def test_prompt_injects_only_configured_dynamic_context() -> None:
    prompt = _prompt(
        _stage(
            LoopContextKind.CHANGED_FILES,
            LoopContextKind.WORKSPACE_DIFF,
            reports=("implementation",),
        )
    )

    assert "Implemented the transfer." in prompt
    assert "42 tests passed." in prompt
    assert "src/app.py (+4/-1)" in prompt
    assert "feature/test @ abc123" in prompt
    assert "resolved when the stage starts" not in prompt


def test_prompt_omits_unconfigured_report_context() -> None:
    prompt = _prompt(_stage(LoopContextKind.WORKSPACE_DIFF))

    assert "feature/test @ abc123" in prompt
    assert "Implemented the transfer." not in prompt
    assert "src/app.py (+4/-1)" not in prompt


def test_workspace_context_uses_live_worktree_state() -> None:
    agent = SimpleNamespace(
        slug="agt-1",
        worktree_slug="loop",
        folder=Path("/repo"),
    )
    store = SimpleNamespace(list_agents_for_work=lambda work_slug: [agent])
    manager = SimpleNamespace(
        ensure=lambda *args: Path("/worktree"),
        describe_state=lambda workdir: WorktreeState(
            workdir=workdir,
            is_git_repo=True,
            branch="feature/test",
            head="abc123",
            status=" M src/app.py\n?? src/new.py",
            changed_files=("src/app.py",),
            untracked_files=("src/new.py",),
        ),
    )

    context = runtime.workspace_prompt_context(
        store,
        manager,
        work_slug="WRK-001",
        agent_slug="agt-1",
    )

    assert "feature/test @ abc123" in context
    assert "M src/app.py" in context
    assert "?? src/new.py" in context
    assert "git diff" in context


# ---------------------------------------------------------------------------
# PR feedback reaches the next stage exactly once, by whichever route applies
# ---------------------------------------------------------------------------

FEEDBACK_NOTE = "\n".join(
    (
        "Address this pull-request feedback in the current worktree:",
        "- FranAguilar at app/kernel_lpn_movement.py:32: rename and move this.",
        "  User instruction: check my changes and make sure tests pass.",
        "- Reviewer at app/moved_lpn.py:8: this snapshot looks wrong.",
        "  User instruction: fix the snapshot.",
    )
)


def _task_stage(*kinds: LoopContextKind, reports: tuple[str, ...] = ()) -> LoopStepDefinition:
    return LoopStepDefinition(
        step_id="implementation",
        name="Implementation",
        kind=LoopStepKind.AGENT_TASK,
        instructions="Implement the target.",
        inputs=tuple(LoopContextReference(kind) for kind in kinds),
        reports=tuple(LoopReportReference(from_stage=item) for item in reports),
    )


def _task_prompt(
    stage: LoopStepDefinition,
    *,
    resolution_note: str = "",
    reports: tuple[StageReportBlock, ...] = (),
) -> str:
    return build_stage_prompt(
        TaskStagePrompt(
            run_id="run-1",
            work_slug="WRK-001",
            artifact_id="objective",
            artifact_title="Goal",
            source_ref="Goal",
            stage=stage,
            reports=reports,
            resolution_note=resolution_note,
        )
    )


def test_a_declared_report_carries_its_content_once() -> None:
    """One renderer, one copy. The old model had a declared `previous_report`
    and an injected corrective note able to carry the same text."""
    prompt = _task_prompt(
        _task_stage(reports=("review",)),
        reports=(StageReportBlock(stage_id="review", summary=FEEDBACK_NOTE),),
    )

    assert prompt.count("Address this pull-request feedback") == 1


def test_an_open_request_is_part_of_the_task_not_the_history() -> None:
    """An outstanding request is the only kind that reads as an instruction,
    so it belongs below the task heading and nowhere above it."""
    prompt = _task_prompt(_task_stage(), resolution_note="Rebase onto master first.")

    assert "Rebase onto master first." in prompt
    assert prompt.index("## Your task now") < prompt.index("Rebase onto master first.")


def test_two_reports_render_as_separate_labelled_blocks() -> None:
    """A reviewer judging whether a correction answered the original finding
    needs both accounts, and has to be able to tell whose is whose."""
    prompt = _task_prompt(
        _task_stage(reports=("implementation", "lint")),
        reports=(
            StageReportBlock(
                stage_id="implementation",
                stage_name="Implementation",
                pass_number=4,
                summary="Restored the order validation.",
            ),
            StageReportBlock(
                stage_id="lint",
                stage_name="Lint",
                pass_number=4,
                summary="Fixed the imports.",
            ),
        ),
    )

    assert "Report -- Implementation (pass 4):" in prompt
    assert "Report -- Lint (pass 4):" in prompt
    assert prompt.index("Implementation (pass 4)") < prompt.index("Lint (pass 4)")


def test_a_declared_report_from_a_stage_that_has_not_run_says_so() -> None:
    prompt = _task_prompt(
        _task_stage(reports=("lint",)),
        reports=(StageReportBlock(stage_id="lint", stage_name="Lint"),),
    )

    assert "This stage has not reported yet." in prompt


def test_dismissed_findings_render_only_when_declared() -> None:
    declared = build_stage_prompt(
        TaskStagePrompt(
            run_id="run-1",
            work_slug="WRK-001",
            artifact_id="objective",
            artifact_title="Goal",
            source_ref="Goal",
            stage=_task_stage(LoopContextKind.WAIVED_FINDINGS),
            waived_findings=("Timeline parity is untested.",),
        )
    )

    assert "Already dismissed by the user" in declared
    assert "Timeline parity is untested." in declared
    # Undeclared input is not rendered: the caller decides by declaration, and
    # the renderer has nothing to suppress.
    assert "Already dismissed" not in _task_prompt(_task_stage())
