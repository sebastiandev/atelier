"""A stored loop must keep the reports its stages declare."""

import tempfile
from dataclasses import replace

from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.dtos import LoopDefinitionScope
from src.infrastructure.filesystem.loop_definitions import FsLoopDefinitionRepository


def _saved(definition):
    """Round-trip one definition through the on-disk repository."""
    with tempfile.TemporaryDirectory() as root:
        repository = FsLoopDefinitionRepository()
        repository.save_definition(root, definition, expected_revision=None)
        return repository.get_definition(root, definition.definition_id)


def test_a_forked_loop_keeps_the_reports_its_stages_declare() -> None:
    """The on-disk codec is a second reader of the same shape. Dropping the
    field there silently erased the declaration of every loop a user forked or
    wrote themselves, leaving their reviewer with no account to read."""
    base = builtin_loop_definition("atelier-reviewed")
    assert base is not None
    fork = replace(base, definition_id="my-loop", scope=LoopDefinitionScope.LIBRARY)

    restored = _saved(fork)

    assert {stage.step_id: stage.reports for stage in restored.stages} == {
        stage.step_id: stage.reports for stage in fork.stages
    }


def test_a_stored_loop_written_before_reports_existed_converts_on_read() -> None:
    base = builtin_loop_definition("atelier-reviewed")
    assert base is not None
    review = next(stage for stage in _saved(
        replace(base, definition_id="my-loop", scope=LoopDefinitionScope.LIBRARY)
    ).stages if stage.step_id == "code-review")

    assert [item.kind.value for item in review.context].count("waived_findings") == 1
    assert [item.kind.value for item in review.context].count("feedback") == 1
