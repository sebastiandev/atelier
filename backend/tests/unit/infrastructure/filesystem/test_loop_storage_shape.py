"""The refactored on-disk shape: layout, inline instructions, use refs, and
the guard that refuses a pre-refactor file rather than misreading it."""

from pathlib import Path

import yaml

from src.domain.loop.dtos import (
    LoopAgentPolicy,
    LoopDefinition,
    LoopDefinitionScope,
    LoopPermission,
    LoopSessionPolicy,
    LoopStepDefinition,
    LoopStepKind,
)
from src.infrastructure.filesystem.loop_definitions import FsLoopDefinitionRepository


def _loop() -> LoopDefinition:
    return LoopDefinition(
        definition_id="demo",
        name="Demo",
        trigger="artifact_or_objective",
        report_schema="",
        description="d",
        scope=LoopDefinitionScope.LIBRARY,
        stages=(
            LoopStepDefinition(
                step_id="implementation",
                name="Implementation",
                kind=LoopStepKind.AGENT_TASK,
                instructions="Implement it.\nCarefully.",
                agent=LoopAgentPolicy(
                    session=LoopSessionPolicy.FRESH, permissions=LoopPermission.WRITE
                ),
                transitions={},
            ),
        ),
    )


def test_library_lives_at_bare_loops_not_dot_atelier(tmp_path: Path) -> None:
    (tmp_path / "loops").mkdir()
    FsLoopDefinitionRepository().save_definition(
        str(tmp_path), _loop(), expected_revision=None
    )

    assert (tmp_path / "loops" / "demo" / "loop.yaml").is_file()
    assert not (tmp_path / ".atelier").exists()


def test_instructions_inline_and_no_steps_dir(tmp_path: Path) -> None:
    (tmp_path / "loops").mkdir()
    repo = FsLoopDefinitionRepository()
    repo.save_definition(str(tmp_path), _loop(), expected_revision=None)

    raw = yaml.safe_load((tmp_path / "loops" / "demo" / "loop.yaml").read_text())
    assert "stages" in raw and "steps" not in raw
    assert "Implement it." in raw["stages"][0]["instructions"]
    assert not (tmp_path / "loops" / "demo" / "steps").exists()

    back = repo.get_definition(str(tmp_path), "demo")
    assert back.stages[0].instructions.strip() == "Implement it.\nCarefully."


def test_a_pre_refactor_file_is_refused_not_misread(tmp_path: Path) -> None:
    """`steps:` is the unambiguous marker of an old file; reading it as the
    new shape would take `steps/x.md` for literal instructions."""
    directory = tmp_path / "loops" / "old"
    (directory / "steps").mkdir(parents=True)
    (directory / "steps" / "implementation.md").write_text("real body\n")
    (directory / "loop.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "old",
                "name": "Old",
                "steps": [
                    {
                        "id": "implementation",
                        "name": "Implementation",
                        "kind": "agent_task",
                        "instructions": "steps/implementation.md",
                    }
                ],
            }
        )
    )

    loaded = FsLoopDefinitionRepository().get_definition(str(tmp_path), "old")

    assert not loaded.valid
    assert any("migrate-loops" in error for error in loaded.errors)
