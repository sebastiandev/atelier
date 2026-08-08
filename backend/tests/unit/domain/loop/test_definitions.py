"""Public contract tests for reusable loop definition validation."""

from dataclasses import replace

import pytest

from src.domain.loop.builtins import builtin_loop_definition, builtin_loop_definitions
from src.domain.loop.definitions import prepare_definition, validate_definition
from src.domain.loop.dtos import (
    LoopContextKind,
    LoopContextReference,
    LoopDefinition,
    LoopReportReference,
)
from src.domain.loop.snapshots import loop_stage_from_document


def test_builtins_are_valid_and_revisioned() -> None:
    definitions = builtin_loop_definitions()

    assert [definition.definition_id for definition in definitions] == [
        "atelier-fast",
        "atelier-reviewed",
        "atelier-secure",
    ]
    assert all(definition.valid and len(definition.revision) == 6 for definition in definitions)
    assert definitions[1].stages[1].retry.timeout_minutes == 15


def test_unsafe_context_path_invalidates_definition() -> None:
    source = builtin_loop_definitions()[1]
    review = source.stages[1]
    unsafe = replace(
        source,
        stages=(
            source.stages[0],
            replace(
                review,
                context=(
                    *review.context,
                    LoopContextReference(
                        LoopContextKind.FILES,
                        paths=("../outside.md",),
                    ),
                ),
            ),
            source.stages[2],
        ),
    )

    prepared = prepare_definition(unsafe)

    assert prepared.valid is False
    assert "unsafe context path" in " ".join(prepared.errors)


def test_review_stage_cannot_be_first() -> None:
    source = builtin_loop_definitions()[1]
    review_first = replace(source, stages=source.stages[1:])

    prepared = prepare_definition(review_first)

    assert "first stage must be an implementation agent" in " ".join(prepared.errors).lower()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"provider": "unknown"}, "unknown provider"),
        ({"provider": "codex", "model": "rush"}, "unsupported model"),
        ({"provider": "amp", "effort": "high"}, "unsupported effort"),
    ],
)
def test_invalid_explicit_agent_override_invalidates_definition(
    changes: dict[str, str],
    message: str,
) -> None:
    source = builtin_loop_definitions()[0]
    stage = source.stages[0]
    assert stage.agent is not None
    invalid = replace(
        source,
        stages=(replace(stage, agent=replace(stage.agent, **changes)), source.stages[1]),
    )

    prepared = prepare_definition(invalid)

    assert message in " ".join(prepared.errors).lower()


def test_repo_scope_value_reads_as_library_for_old_snapshots() -> None:
    """`repo` was renamed to `library`; historical snapshots still say repo."""
    from src.domain.loop.dtos import LoopDefinitionScope, StageDefinitionScope

    assert LoopDefinitionScope("repo") is LoopDefinitionScope.LIBRARY
    assert LoopDefinitionScope("library") is LoopDefinitionScope.LIBRARY
    assert StageDefinitionScope("repo") is StageDefinitionScope.LIBRARY


def test_slugify_definition_id_matches_the_editor_rule() -> None:
    from src.domain.loop.definitions import slugify_definition_id

    assert slugify_definition_id("ShipHero Code & Review") == "shiphero-code-review"
    assert slugify_definition_id("  Spaces --and-- symbols!! ") == "spaces-and-symbols"
    assert slugify_definition_id("Shiphero-code") == "shiphero-code"
    assert slugify_definition_id("!!!") == ""


def _reporting_loop(from_stage: str) -> LoopDefinition:
    base = builtin_loop_definition("atelier-reviewed")
    assert base is not None
    stages = tuple(
        replace(stage, reports=(LoopReportReference(from_stage=from_stage),))
        if stage.step_id == "implementation"
        else stage
        for stage in base.stages
    )
    return replace(base, stages=stages)


def test_reading_the_review_that_sends_work_back_is_valid() -> None:
    """A loop is cyclic. By the time the implementation runs again the review
    really has reported, and naming it is the whole point of declaring a report
    rather than taking whichever stage ran last."""
    assert validate_definition(_reporting_loop("code-review")) == ()


def test_a_report_from_an_unknown_stage_is_rejected() -> None:
    errors = validate_definition(_reporting_loop("nonexistent"))

    assert any("unknown stage" in error for error in errors)


def test_the_symbolic_previous_is_always_valid() -> None:
    assert validate_definition(_reporting_loop("previous")) == ()


def test_a_stored_loop_with_an_unreadable_value_is_refused_on_read() -> None:
    """Validation on load is the coverage: the editor cannot write one, so a bad
    value only arrives by hand-editing or import, and both read through here."""
    with pytest.raises(ValueError, match="none, summaries, or full"):
        loop_stage_from_document(
            {
                "id": "implementation",
                "name": "Implementation",
                "kind": "agent_task",
                "context": [],
                "reports": [],
                "history": "sumaries",
                "transitions": {},
            }
        )
