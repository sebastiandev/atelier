"""Public contract tests for reusable loop definition validation."""

from dataclasses import replace

import pytest

from src.domain.loop.builtins import builtin_loop_definitions
from src.domain.loop.definitions import prepare_definition
from src.domain.loop.dtos import LoopContextKind, LoopContextReference


def test_builtins_are_valid_and_revisioned() -> None:
    definitions = builtin_loop_definitions()

    assert [definition.definition_id for definition in definitions] == [
        "atelier-fast",
        "atelier-reviewed",
        "atelier-secure",
    ]
    assert all(definition.valid and len(definition.revision) == 6 for definition in definitions)


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

    assert "first stage must be an implementation agent" in " ".join(
        prepared.errors
    ).lower()


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
