"""Tests for configured dynamic Loop prompt context."""

from pathlib import Path
from types import SimpleNamespace

from src.domain.loop import runtime
from src.domain.loop.dtos import (
    LoopContextKind,
    LoopContextReference,
    LoopReportReference,
    LoopStepDefinition,
    PrStage,
    ReviewStage,
    TaskStage,
)
from src.domain.loop.prompts import (
    ReviewStagePrompt,
    StageReportBlock,
    TaskStagePrompt,
    build_follow_up_prompt,
    build_stage_prompt,
    stage_report_repair_prompt,
)
from src.domain.worktrees import WorktreeState


def _stage(*kinds: LoopContextKind, reports: tuple[str, ...] = ()) -> LoopStepDefinition:
    """Return a review stage with the selected input and report contract."""
    return ReviewStage(
        step_id="review",
        name="Review",
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
    return TaskStage(
        step_id="implementation",
        name="Implementation",
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


def _follow_up(
    stage: LoopStepDefinition,
    *,
    note: str = "Rename the endpoint.",
    waived_findings: tuple[str, ...] = (),
) -> str:
    """Render the follow-up a resumed stage receives."""
    return build_follow_up_prompt(
        TaskStagePrompt(
            run_id="run-1",
            work_slug="WRK-001",
            artifact_id="objective",
            artifact_title="Goal",
            source_ref="Goal",
            stage=stage,
            resolution_note=note,
            waived_findings=waived_findings,
        )
    )


def test_follow_up_carries_the_request_and_the_report_contract() -> None:
    prompt = _follow_up(_task_stage())

    assert "Rename the endpoint." in prompt
    # The agent still has to answer in the report shape, and it is the one part
    # of the seed it may consider finished with.
    assert "single-line JSON report" in prompt


def test_follow_up_omits_context_the_resumed_session_already_holds() -> None:
    prompt = _follow_up(
        _task_stage(LoopContextKind.WORKSPACE_DIFF, LoopContextKind.CHANGED_FILES)
    )

    assert "Implement the target." not in prompt
    assert "Execute loop run" not in prompt
    assert "What has already happened" not in prompt
    assert "Current workspace diff" not in prompt
    assert "Changed files from prior stage reports" not in prompt


def test_follow_up_states_the_request_once() -> None:
    prompt = _follow_up(_task_stage(), note="Rename the endpoint.")

    assert prompt.count("Rename the endpoint.") == 1


def test_follow_up_without_a_note_still_asks_the_stage_to_continue() -> None:
    prompt = _follow_up(_task_stage(), note="")

    assert "Continue this stage." in prompt


def test_follow_up_renders_newly_dismissed_findings() -> None:
    prompt = _follow_up(_task_stage(), waived_findings=("Timeline parity is untested.",))

    assert "Already dismissed by the user" in prompt
    assert "Timeline parity is untested." in prompt


def test_follow_up_carries_reports_written_while_the_session_waited() -> None:
    prompt = build_follow_up_prompt(
        TaskStagePrompt(
            run_id="run-1",
            work_slug="WRK-001",
            artifact_id="objective",
            artifact_title="Goal",
            source_ref="Goal",
            stage=_task_stage(reports=("code-review",)),
            reports=(
                StageReportBlock(
                    stage_id="code-review",
                    stage_name="Code review",
                    summary="The writer path is unguarded.",
                    findings=("Fix the race.",),
                ),
            ),
            resolution_note="Address the review.",
        )
    )

    # A review's verdict is written by another agent, so the resumed session
    # never saw it -- it is the usual reason the user asks for changes.
    assert "The writer path is unguarded." in prompt
    assert "Fix the race." in prompt


def test_follow_up_carries_what_did_not_resolve_but_not_the_index() -> None:
    prompt = build_follow_up_prompt(
        TaskStagePrompt(
            run_id="run-1",
            work_slug="WRK-001",
            artifact_id="objective",
            artifact_title="Goal",
            source_ref="Goal",
            stage=_task_stage(LoopContextKind.WAIVED_FINDINGS),
            resolved_context=("docs/architecture.md",),
            context_warnings=("required report from build: never reported",),
            resolution_note="Continue.",
        )
    )

    # Whether a declared report arrived depends on which stage sent this one
    # back, so it is recomputed per entry and can be news.
    assert "required report from build: never reported" in prompt
    # The index resolved once when the run started, so this session already
    # has it and repeating it says nothing.
    assert "docs/architecture.md" not in prompt


def test_follow_up_omits_the_context_index_when_nothing_resolved() -> None:
    prompt = _follow_up(_task_stage(LoopContextKind.WAIVED_FINDINGS))

    # The unresolved fallback lists the stage's own declarations back at it
    # ("target (optional): backend-resolved"), which reads as though context
    # had failed to resolve. A resumed session already has the real index.
    assert "Context references" not in prompt
    assert "backend-resolved" not in prompt


def test_only_the_pr_stage_is_asked_for_per_comment_replies() -> None:
    """The report example is rendered last, under "respond with exactly one
    report using this shape", so a field asked for only in prose upstream gets
    dropped -- which left every PR comment with a bare commit citation and
    nothing said about it."""
    pr_stage = PrStage(step_id="create-pr", name="Create PR")

    assert "comment_replies" in stage_report_repair_prompt(pr_stage)
    assert "comment_replies" not in stage_report_repair_prompt(_stage())
