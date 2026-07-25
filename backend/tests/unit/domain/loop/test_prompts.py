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
from src.domain.loop.prompts import ReviewStagePrompt, build_stage_prompt
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
