"""Tests for configured dynamic Loop prompt context."""

from pathlib import Path
from types import SimpleNamespace

from src.domain.loop import runtime
from src.domain.loop.dtos import (
    LoopContextKind,
    LoopContextReference,
    LoopStepDefinition,
    LoopStepKind,
)
from src.domain.loop.prompts import (
    ReviewStagePrompt,
    TaskStagePrompt,
    build_stage_prompt,
)
from src.domain.worktrees import WorktreeState


def _stage(*kinds: LoopContextKind) -> LoopStepDefinition:
    """Return a review stage with the selected context contract."""
    return LoopStepDefinition(
        step_id="review",
        name="Review",
        kind=LoopStepKind.AGENT_REVIEW,
        instructions="Review the implementation.",
        context=tuple(LoopContextReference(kind) for kind in kinds),
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
            previous_summary="Implemented the transfer.",
            previous_findings=("One finding.",),
            previous_validation_evidence="42 tests passed.",
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
            LoopContextKind.PREVIOUS_REPORT,
            LoopContextKind.CHANGED_FILES,
            LoopContextKind.WORKSPACE_DIFF,
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


def _task_stage(*kinds: LoopContextKind) -> LoopStepDefinition:
    return LoopStepDefinition(
        step_id="implementation",
        name="Implementation",
        kind=LoopStepKind.AGENT_TASK,
        instructions="Implement the target.",
        context=tuple(LoopContextReference(kind) for kind in kinds),
    )


def _task_prompt(stage: LoopStepDefinition, *, resolution_note: str = "") -> str:
    return build_stage_prompt(
        TaskStagePrompt(
            run_id="run-1",
            work_slug="WRK-001",
            artifact_id="objective",
            artifact_title="Goal",
            source_ref="Goal",
            stage=stage,
            previous_summary=FEEDBACK_NOTE,
            resolution_note=resolution_note,
        )
    )


def test_feedback_appears_once_when_the_stage_declares_previous_report() -> None:
    """The previous block carries it, so the monitor adds no note."""
    prompt = _task_prompt(_task_stage(LoopContextKind.PREVIOUS_REPORT))

    assert prompt.count("Address this pull-request feedback") == 1


def test_feedback_appears_once_when_the_stage_declares_no_context() -> None:
    """`_changes_requested_note` is the only carrier — the shape your
    implementation stage has, with `context: []`."""
    prompt = _task_prompt(
        _task_stage(),
        resolution_note=f"Required changes from the prior stage:\nSummary: {FEEDBACK_NOTE}",
    )

    assert prompt.count("Address this pull-request feedback") == 1


def test_a_resolution_note_is_still_rendered_for_other_flows() -> None:
    """Dropping the PR-feedback copy must not mute a human's resume note or a
    review gate instruction, which use the same channel."""
    prompt = _task_prompt(_task_stage(), resolution_note="Rebase onto master first.")

    assert "User resolution:" in prompt
    assert "Rebase onto master first." in prompt
