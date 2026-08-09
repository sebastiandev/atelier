"""Public contract tests for reusable standalone stages."""

from dataclasses import replace

import pytest

from src.domain.loop.dtos import (
    LoopContextKind,
    LoopContextReference,
    LoopHistoryLevel,
    LoopOutcome,
    LoopPrConfig,
    LoopReportReference,
    ReviewStage,
    StageDefinitionRef,
    StageOverrides,
    TaskStage,
)
from src.domain.loop.snapshots import (
    loop_stage_from_document,
    loop_stage_from_snapshot,
    loop_stage_snapshot,
    stage_overrides_from_snapshot,
    stage_overrides_snapshot,
)
from src.domain.loop.stage_builtins import builtin_stage_definition
from src.domain.loop.stages import (
    StageDefinitionInvalid,
    prepare_stage_definition,
    resolve_stage_link,
)


def test_pr_stage_rejects_incomplete_persisted_configuration() -> None:
    source = builtin_stage_definition("create-pr")
    assert source is not None
    invalid = replace(
        source,
        stage=replace(
            source.stage,
            pr_config=LoopPrConfig(
                name_template="",
                description_mode="manual",
                manual_body="",
                base_branch="",
            ),
        ),
    )

    prepared = prepare_stage_definition(invalid)

    assert prepared.valid is False
    assert "name template" in " ".join(prepared.errors)
    assert "base branch" in " ".join(prepared.errors)
    assert "Markdown content" in " ".join(prepared.errors)


def test_link_resolution_keeps_loop_wiring_and_applies_sparse_overrides() -> None:
    source = builtin_stage_definition("code-review")
    assert source is not None
    assert source.stage.retry.timeout_minutes == 15
    instance = replace(
        source.stage,
        step_id="review-api",
        transitions={},
        stage_ref=StageDefinitionRef(source.definition_id, source.revision),
        overrides=StageOverrides(name="API review"),
    )

    resolved = resolve_stage_link(instance, source)

    assert resolved.step_id == "review-api"
    assert resolved.name == "API review"
    assert resolved.stage_ref == StageDefinitionRef(source.definition_id, source.revision)


def test_unrouted_outcome_does_not_require_the_stage_to_declare_it() -> None:
    """A ``-`` (null destination) transition is not wired, so an undeclared
    outcome is allowed — matching inline-stage validation."""
    source = builtin_stage_definition("implement")
    assert source is not None
    assert LoopOutcome.CHANGES_REQUESTED not in source.outcomes
    instance = replace(
        source.stage,
        step_id="implement",
        transitions={
            LoopOutcome.PASS: "code-review",
            LoopOutcome.CHANGES_REQUESTED: None,  # the UI's "-"
        },
        stage_ref=StageDefinitionRef(source.definition_id, source.revision),
    )

    resolved = resolve_stage_link(instance, source)

    assert resolved.transitions[LoopOutcome.PASS] == "code-review"
    assert resolved.transitions[LoopOutcome.CHANGES_REQUESTED] is None


def test_routed_undeclared_outcome_is_still_rejected() -> None:
    """A wired transition to an outcome the stage cannot produce is invalid."""
    source = builtin_stage_definition("implement")
    assert source is not None
    instance = replace(
        source.stage,
        step_id="implement",
        transitions={LoopOutcome.CHANGES_REQUESTED: "implement"},
        stage_ref=StageDefinitionRef(source.definition_id, source.revision),
    )

    with pytest.raises(StageDefinitionInvalid, match="changes_requested"):
        resolve_stage_link(instance, source)


def test_a_legacy_previous_report_context_reads_as_a_declared_report() -> None:
    """The one seam. Everything downstream only ever sees the new shape."""
    stage = loop_stage_from_snapshot(
        {
            "id": "code-review",
            "name": "Code review",
            "kind": "agent_review",
            "context": [
                {"kind": "workspace_diff", "required": True},
                {"kind": "previous_report", "required": True, "step": "implementation"},
            ],
            "transitions": {},
        }
    )

    assert stage.reports == (LoopReportReference(from_stage="implementation"),)
    # Feedback and dismissed findings used to be injected by stage kind. A
    # review written before inputs were declared is granted both, or it would
    # start re-raising findings the user had already waived.
    assert [item.kind.value for item in stage.inputs] == [
        "workspace_diff",
        "feedback",
        "waived_findings",
    ]


def test_a_legacy_previous_report_without_a_step_reads_as_previous() -> None:
    stage = loop_stage_from_snapshot(
        {
            "id": "create-pr",
            "name": "Create PR",
            "kind": "pr",
            "context": [{"kind": "previous_report", "required": True}],
            "transitions": {},
        }
    )

    assert stage.reports == (LoopReportReference(from_stage="previous"),)
    # A publishing stage is not granted feedback: handing it the open block is
    # what made it report changes_requested and cycle the run.
    assert stage.inputs == ()


def test_a_stage_written_with_declarations_is_taken_at_its_word() -> None:
    stage = loop_stage_from_snapshot(
        {
            "id": "publish",
            "name": "Publish",
            "kind": "agent_task",
            "context": [],
            "reports": [{"from": "implementation", "required": False}],
            "transitions": {},
        }
    )

    assert stage.reports == (LoopReportReference(from_stage="implementation", required=False),)
    assert stage.inputs == ()


def test_declarations_round_trip_through_a_snapshot() -> None:
    stage = ReviewStage(
        step_id="review",
        name="Review",
        instructions="Review it.",
        inputs=(LoopContextReference(LoopContextKind.WAIVED_FINDINGS),),
        reports=(
            LoopReportReference(from_stage="implementation"),
            LoopReportReference(from_stage="lint", required=False),
        ),
        history=LoopHistoryLevel.SUMMARIES,
    )

    restored = loop_stage_from_snapshot(loop_stage_snapshot(stage))

    assert restored.reports == stage.reports
    assert restored.history == LoopHistoryLevel.SUMMARIES
    assert restored.inputs == stage.inputs


def test_a_stage_that_declares_no_feedback_keeps_declaring_none() -> None:
    """The declaration has to survive its own serializer. Keying "written
    before inputs existed" on a field the writer omitted when empty made a
    modern stage indistinguishable from a legacy one, so feedback came back."""
    stage = TaskStage(
        step_id="publish",
        name="Publish",
        instructions="Publish it.",
    )

    once = loop_stage_from_snapshot(loop_stage_snapshot(stage))
    twice = loop_stage_from_snapshot(loop_stage_snapshot(once))

    assert once.inputs == ()
    assert twice.inputs == ()


def test_a_legacy_review_still_gets_the_findings_the_user_dismissed() -> None:
    """Dismissed findings used to reach every review by stage kind. Without
    the same grant, every loop a user already has starts re-raising them."""
    stage = loop_stage_from_snapshot(
        {
            "id": "code-review",
            "name": "Code review",
            "kind": "agent_review",
            "context": [{"kind": "workspace_diff", "required": True}],
            "transitions": {},
        }
    )

    assert [item.kind.value for item in stage.inputs] == [
        "workspace_diff",
        "feedback",
        "waived_findings",
    ]


def test_overrides_carry_reports_and_history() -> None:
    overrides = StageOverrides(
        reports=(LoopReportReference(from_stage="implementation"),),
        history=LoopHistoryLevel.SUMMARIES,
    )

    restored = stage_overrides_from_snapshot(stage_overrides_snapshot(overrides))

    assert restored is not None
    assert restored.reports == overrides.reports
    assert restored.history == LoopHistoryLevel.SUMMARIES


def test_a_legacy_override_context_is_normalised_like_a_stage_body() -> None:
    """An override replaces the base's inputs wholesale, so an unconverted one
    loses both the report it declared and the inputs the base was granted."""
    restored = stage_overrides_from_snapshot(
        {
            "context": [
                {"kind": "workspace_diff", "required": True},
                {"kind": "previous_report", "required": True, "step": "implementation"},
            ]
        }
    )

    assert restored is not None
    assert restored.reports == (LoopReportReference(from_stage="implementation"),)
    assert [item.kind.value for item in (restored.inputs or ())] == [
        "workspace_diff",
        "feedback",
    ]


def test_a_document_refuses_a_history_level_that_is_not_one() -> None:
    """A `loop.yaml` or an imported document is something the user can fix, so
    the reader says so instead of quietly running without the history asked for.
    Both callers already turn this into an invalid definition with the message."""
    with pytest.raises(ValueError, match="none, summaries, or full"):
        loop_stage_from_document(
            {
                "id": "review",
                "name": "Review",
                "kind": "agent_review",
                "context": [],
                "reports": [],
                "history": "sumaries",
                "transitions": {},
            }
        )


def test_a_pinned_run_snapshot_still_loads_what_it_cannot_honour() -> None:
    """A run already in flight cannot be fixed by refusing to load it."""
    stage = loop_stage_from_snapshot(
        {
            "id": "review",
            "name": "Review",
            "kind": "agent_review",
            "context": [],
            "reports": [],
            "history": "sumaries",
            "transitions": {},
        }
    )

    assert stage.history == LoopHistoryLevel.NONE


def test_a_readable_history_level_is_taken_as_written() -> None:
    stage = loop_stage_from_document(
        {
            "id": "review",
            "name": "Review",
            "kind": "agent_review",
            "context": [],
            "reports": [],
            "history": "full",
            "transitions": {},
        }
    )

    assert stage.history == LoopHistoryLevel.FULL
