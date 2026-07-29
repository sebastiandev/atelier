"""Tests for Planning materializer prompt boundaries."""

from src.domain.planning.prompts import (
    PlanningMaterializationPrompt,
    PlanningMaterializationRecoveryPrompt,
    PlanningMaterializationReportPrompt,
    PlanningMaterializerRuntimePrompt,
)
from src.domain.prompts import build_prompt


def test_materializer_prompt_keeps_framework_separate_from_output_path() -> None:
    prompt = build_prompt(
        PlanningMaterializationPrompt(
            work_slug="WRK-002",
            work_name="Migrate data",
            root_path="/repo",
            atelier_planning_path="/repo/.atelier/planning/WRK-002",
            plan_artifacts_dir="bmad/di_migration",
            framework="openspec",
            profile="feature",
            planning_chat_slug="CHT-001",
            planning_chat_excerpt="Use a proposal and spec deltas.",
        )
    )

    assert "OpenSpec is the authoritative framework" in prompt
    assert "folder does not identify or change the selected framework" in prompt


def test_materializer_runtime_prompt_is_bounded_and_framework_authoritative() -> None:
    prompt = build_prompt(
        PlanningMaterializerRuntimePrompt(
            framework="openspec",
            root_path="/repo",
            plan_artifacts_dir="bmad/di_migration",
        )
    )

    assert "selected framework is OpenSpec" in prompt
    assert "folders and files are paths only" in prompt
    assert "Inventory the planning output once" in prompt
    assert "below 12,000 characters" in prompt


def test_materializer_recovery_prompt_reseeds_original_brief() -> None:
    prompt = build_prompt(
        PlanningMaterializationRecoveryPrompt(
            framework="spec",
            plan_artifacts_dir="bmad/di_migration",
            original_brief="Original discovery and materialization requirements.",
        )
    )

    assert "selected framework is Spec-kit" in prompt
    assert "only a path" in prompt
    assert "Original discovery and materialization requirements." in prompt
    assert "Do not restart broad discovery" in prompt
    assert "Do not read framework skill files" in prompt
    assert "Read the output README or index first" in prompt


def test_materializer_report_prompt_forbids_replanning() -> None:
    prompt = build_prompt(
        PlanningMaterializationReportPrompt(
            framework="spec",
            plan_artifacts_dir="bmad/di_migration",
        )
    )

    assert "selected framework remains Spec-kit" in prompt
    assert "Do not restart planning, scan the repository" in prompt
    assert "atelier_plan_materialization" in prompt
