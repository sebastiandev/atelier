"""The import commit rolls back every stage it created when one fails."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.domain.commands.loops import import_definition
from src.domain.loop.dtos import (
    LoopAgentPolicy,
    LoopDefinition,
    LoopOutcome,
    LoopPermission,
    LoopReportField,
    LoopReportSchema,
    LoopStepDefinition,
    LoopStepKind,
    StageDefinition,
    StageDefinitionRef,
)
from src.domain.loop.stages import StageDefinitionInvalid
from src.domain.loop.transport import export_loop

_REPORT = LoopReportSchema(
    schema_id="multi-stage-loop-report",
    fields=(LoopReportField("summary", "Summary", allow_explicit_none=False),),
)


class _FakeStageRepo:
    """Records stage writes/deletes and fails a chosen save."""

    def __init__(self, fail_on: int) -> None:
        self.saved: list[str] = []
        self.deleted: list[str] = []
        self._calls = 0
        self._fail_on = fail_on

    def list_definitions(self, root: str, *, scope: object = None) -> list[StageDefinition]:
        return []

    def get_definition(
        self, root: str, definition_id: str, *, scope: object = None
    ) -> StageDefinition | None:
        return None

    def save_definition(
        self, root: str, definition: StageDefinition, *, expected_revision: str | None
    ) -> StageDefinition:
        self._calls += 1
        if self._calls == self._fail_on:
            raise StageDefinitionInvalid("forced failure")
        self.saved.append(definition.definition_id)
        return replace(definition, revision=f"rev-{definition.definition_id}")

    def delete_definition(self, root: str, definition_id: str) -> None:
        self.deleted.append(definition_id)

    def definition_dir(self, root: str, definition_id: str) -> Path:
        return Path(root) / definition_id


class _FakeLoopRepo:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def list_definitions(self, root: str, *, scope: object = None) -> list[LoopDefinition]:
        return []

    def get_definition(
        self, root: str, definition_id: str, *, scope: object = None
    ) -> LoopDefinition | None:
        return None

    def save_definition(
        self,
        root: str,
        definition: LoopDefinition,
        *,
        expected_revision: str | None,
        scope: object = None,
    ) -> LoopDefinition:
        self.saved.append(definition.definition_id)
        return definition

    def delete_definition(self, root: str, definition_id: str) -> None:  # pragma: no cover
        pass

    def definition_dir(self, root: str, definition_id: str) -> Path:  # pragma: no cover
        return Path(root) / definition_id


class _FakeLocations:
    def loop_library_root(self) -> str:
        return "/tmp/atelier-import-test"

    def work_loop_root(self, work_slug: str) -> str:  # pragma: no cover
        return f"/tmp/{work_slug}"


def _two_linked_stage_document() -> dict[str, object]:
    impl = LoopStepDefinition(
        step_id="impl",
        name="Impl",
        kind=LoopStepKind.AGENT_TASK,
        instructions="Do the work.",
        agent=LoopAgentPolicy(permissions=LoopPermission.READ),
        transitions={LoopOutcome.PASS: "check-one"},
    )
    check_one = LoopStepDefinition(
        step_id="check-one",
        name="Check one",
        kind=LoopStepKind.DETERMINISTIC_CHECK,
        check_adapter="command",
        check_command=("a",),
        transitions={LoopOutcome.PASS: "check-two"},
        stage_ref=StageDefinitionRef("check-one", "r1"),
    )
    check_two = LoopStepDefinition(
        step_id="check-two",
        name="Check two",
        kind=LoopStepKind.DETERMINISTIC_CHECK,
        check_adapter="command",
        check_command=("b",),
        transitions={LoopOutcome.PASS: "approval"},
        stage_ref=StageDefinitionRef("check-two", "r2"),
    )
    approval = LoopStepDefinition(
        step_id="approval",
        name="Approve",
        kind=LoopStepKind.USER_APPROVAL,
        transitions={LoopOutcome.PASS: "complete"},
    )
    loop = LoopDefinition(
        definition_id="two-check",
        name="Two check",
        trigger="artifact_or_objective",
        report_schema=_REPORT,
        stages=(impl, check_one, check_two, approval),
    )
    sources = {
        "check-one": StageDefinition(
            definition_id="check-one",
            name="Check one",
            outcomes=(LoopOutcome.PASS,),
            stage=check_one,
        ),
        "check-two": StageDefinition(
            definition_id="check-two",
            name="Check two",
            outcomes=(LoopOutcome.PASS,),
            stage=check_two,
        ),
    }
    return export_loop(loop, sources)


def test_commit_rolls_back_created_stages_when_one_fails() -> None:
    document = _two_linked_stage_document()
    stage_repo = _FakeStageRepo(fail_on=2)  # the second stage create raises
    loop_repo = _FakeLoopRepo()

    with pytest.raises(StageDefinitionInvalid):
        import_definition.commit(
            locations=_FakeLocations(),
            loop_repository=loop_repo,  # type: ignore[arg-type]
            stage_repository=stage_repo,  # type: ignore[arg-type]
            req=import_definition.ImportLoopRequest(
                document=document, name_override="Two check"
            ),
        )

    # The first stage was created then rolled back; the loop was never written.
    assert stage_repo.saved == ["check-one"]
    assert stage_repo.deleted == ["check-one"]
    assert loop_repo.saved == []
