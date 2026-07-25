"""Public contract tests for reusable standalone stages."""

from dataclasses import replace

from src.domain.loop.dtos import LoopPrConfig, StageDefinitionRef, StageOverrides
from src.domain.loop.stage_builtins import builtin_stage_definition
from src.domain.loop.stages import prepare_stage_definition, resolve_stage_link


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
