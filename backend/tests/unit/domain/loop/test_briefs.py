"""Tests for Work-owned loop brief resolution."""

from dataclasses import replace

from src.domain.loop.briefs import resolved_approved_command_prefixes
from src.domain.loop.builtins import builtin_loop_definitions
from src.domain.loop.dtos import LoopBrief, LoopStageBrief


def test_command_prefixes_inherit_from_first_stage_and_allow_work_override() -> None:
    source = builtin_loop_definitions()[1]
    implementation, review, approval = source.stages
    assert implementation.agent is not None
    assert review.agent is not None
    implementation = replace(
        implementation,
        agent=replace(
            implementation.agent,
            approved_command_prefixes=("dt sh -s app-endpoints",),
        ),
    )
    # The built-in review stage now carries its own read-only set, so clear it
    # to exercise inheritance; the case where a stage keeps its own is below.
    review = replace(
        review,
        agent=replace(review.agent, approved_command_prefixes=None),
    )
    definition = replace(
        source,
        stages=(implementation, review, approval),
    )

    inherited = resolved_approved_command_prefixes(definition, None, review)
    overridden = resolved_approved_command_prefixes(
        definition,
        LoopBrief(
            goal="Test",
            stages=(
                LoopStageBrief(
                    review.step_id,
                    approved_command_prefixes=("git diff",),
                ),
            ),
        ),
        review,
    )

    assert inherited == ("dt sh -s app-endpoints",)
    assert overridden == ("git diff",)


def test_a_stage_with_its_own_prefixes_does_not_inherit() -> None:
    """The built-in review stage declares read-only commands. Inheriting the
    implementation stage's set instead would hand the reviewer its test
    runner, which is the opposite of what a read stage wants."""
    definition = builtin_loop_definitions()[1]
    implementation, review, _approval = definition.stages
    assert implementation.agent is not None
    assert review.agent is not None

    resolved = resolved_approved_command_prefixes(definition, None, review)

    assert resolved == review.agent.approved_command_prefixes
    assert "rg" in resolved
    assert not any(prefix.startswith("dt pytest") for prefix in resolved)
