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
