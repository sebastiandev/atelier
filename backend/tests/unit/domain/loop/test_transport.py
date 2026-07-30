"""Unit tests for the pure loop/stage import-export transport."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.domain.loop.dtos import (
    LoopAgentPolicy,
    LoopDefinition,
    LoopDefinitionScope,
    LoopOutcome,
    LoopPermission,
    LoopReportField,
    LoopReportSchema,
    LoopStepDefinition,
    LoopStepKind,
    StageDefinition,
    StageDefinitionRef,
    StageOverrides,
)
from src.domain.loop.transport import (
    StageImportStatus,
    TransportInvalid,
    classify_linked_stage,
    export_loop,
    export_stage,
    overrides_from_drift,
    parse_loop_import,
    parse_stage_import,
    reconstituted_stage_definition,
    stage_command_prefixes,
    stage_grants_write,
)

_REPORT = LoopReportSchema(
    schema_id="multi-stage-loop-report",
    fields=(LoopReportField("summary", "Summary", allow_explicit_none=False),),
)


def _inline_task() -> LoopStepDefinition:
    return LoopStepDefinition(
        step_id="implementation",
        name="Implementation",
        kind=LoopStepKind.AGENT_TASK,
        instructions="Do the work.",
        agent=LoopAgentPolicy(
            permissions=LoopPermission.WRITE,
            approved_command_prefixes=("git add", "git commit"),
        ),
        transitions={LoopOutcome.PASS: "checks"},
    )


def _linked_check() -> LoopStepDefinition:
    return LoopStepDefinition(
        step_id="checks",
        name="Checks",
        kind=LoopStepKind.DETERMINISTIC_CHECK,
        check_adapter="command",
        check_command=("pytest",),
        transitions={LoopOutcome.PASS: "approval"},
        stage_ref=StageDefinitionRef("checks", "abc123"),
    )


def _check_source(revision: str = "abc123") -> StageDefinition:
    return StageDefinition(
        definition_id="checks",
        name="Checks",
        description="Run the checks.",
        scope=LoopDefinitionScope.LIBRARY,  # type: ignore[arg-type]
        revision=revision,
        outcomes=(LoopOutcome.PASS, LoopOutcome.FAILED),
        stage=LoopStepDefinition(
            step_id="checks",
            name="Checks",
            kind=LoopStepKind.DETERMINISTIC_CHECK,
            check_adapter="command",
            check_command=("pytest",),
        ),
    )


def _resolved_loop() -> LoopDefinition:
    return LoopDefinition(
        definition_id="shiphero-code",
        name="Shiphero code",
        trigger="artifact_or_objective",
        report_schema=_REPORT,
        description="A shareable loop.",
        stages=(_inline_task(), _linked_check()),
    )


def test_export_marks_only_linked_stages_with_provenance() -> None:
    document = export_loop(_resolved_loop(), {"checks": _check_source()})

    assert document["kind"] == "loop"
    inline, linked = document["stages"]
    assert "linked_from" not in inline
    assert "stage_ref" not in inline
    assert linked["linked_from"] == "checks@abc123"
    assert linked["outcomes"] == ["pass", "failed"]
    assert linked["description"] == "Run the checks."
    assert "stage_ref" not in linked


def test_export_falls_back_to_transition_keys_without_a_source() -> None:
    document = export_loop(_resolved_loop(), {})

    linked = document["stages"][1]
    assert linked["linked_from"] == "checks@abc123"
    assert linked["outcomes"] == ["pass"]  # derived from transitions


def test_loop_document_round_trips_through_parse() -> None:
    document = export_loop(_resolved_loop(), {"checks": _check_source()})

    parsed = parse_loop_import(document)

    assert parsed.name == "Shiphero code"
    assert parsed.description == "A shareable loop."
    inline, linked = parsed.stages
    assert inline.linked_from is None
    assert inline.stage.step_id == "implementation"
    assert linked.linked_from == StageDefinitionRef("checks", "abc123")
    assert linked.source_outcomes == (LoopOutcome.PASS, LoopOutcome.FAILED)
    assert linked.source_description == "Run the checks."
    assert linked.stage.transitions == {LoopOutcome.PASS: "approval"}


def test_stage_document_round_trips() -> None:
    document = export_stage(_check_source())

    parsed = parse_stage_import(document)

    assert document["kind"] == "stage"
    assert parsed.name == "Checks"
    assert parsed.outcomes == (LoopOutcome.PASS, LoopOutcome.FAILED)
    assert parsed.stage.check_command == ("pytest",)


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        (None, StageImportStatus.LINK_CLEAN),
        (_check_source("abc123"), StageImportStatus.LINK_CLEAN),
        (_check_source("zzz999"), StageImportStatus.LINK_CONFLICT),
    ],
)
def test_classify_linked_stage(
    local: StageDefinition | None, expected: StageImportStatus
) -> None:
    imported = parse_loop_import(
        export_loop(_resolved_loop(), {"checks": _check_source()})
    ).stages[1]

    assert classify_linked_stage(imported, local) == expected


def test_classify_inline_stage_is_inline() -> None:
    imported = parse_loop_import(
        export_loop(_resolved_loop(), {"checks": _check_source()})
    ).stages[0]

    assert classify_linked_stage(imported, None) == StageImportStatus.INLINE


def test_safety_surface_reads_agent_policy() -> None:
    assert stage_command_prefixes(_inline_task()) == ("git add", "git commit")
    assert stage_grants_write(_inline_task()) is True
    assert stage_command_prefixes(_linked_check()) == ()
    assert stage_grants_write(_linked_check()) is False


def test_reconstitution_strips_loop_wiring() -> None:
    imported = parse_loop_import(
        export_loop(_resolved_loop(), {"checks": _check_source()})
    ).stages[1]

    definition = reconstituted_stage_definition(imported, "checks")

    assert definition.definition_id == "checks"
    assert definition.outcomes == (LoopOutcome.PASS, LoopOutcome.FAILED)
    assert definition.stage.step_id == "checks"
    assert definition.stage.transitions == {}
    assert definition.stage.stage_ref is None


def test_linked_stage_overrides_are_recovered_by_drift() -> None:
    """A linked stage renamed/re-instructed by the loop exports its effective
    body; import recovers the loop-local overrides by diffing it vs the base,
    so a same-rev link is not reverted to base content."""
    base = LoopStepDefinition(
        step_id="implement",
        name="Implement",
        kind=LoopStepKind.AGENT_TASK,
        instructions="Generic implementation.",
        agent=LoopAgentPolicy(permissions=LoopPermission.WRITE),
    )
    source = StageDefinition(
        definition_id="implement",
        name="Implement",
        description="Base implement.",
        revision="6105f3",
        outcomes=(LoopOutcome.PASS, LoopOutcome.FAILED),
        stage=base,
    )
    linked = LoopStepDefinition(
        step_id="implement",
        name="Apply lint & typing fixes",
        kind=LoopStepKind.AGENT_TASK,
        instructions="Fix only the reported lints.",
        agent=LoopAgentPolicy(permissions=LoopPermission.WRITE),
        transitions={LoopOutcome.PASS: "approval"},
        stage_ref=StageDefinitionRef("implement", "6105f3"),
        overrides=StageOverrides(
            name="Apply lint & typing fixes",
            instructions="Fix only the reported lints.",
        ),
    )
    loop = LoopDefinition(
        definition_id="fixes",
        name="Fixes",
        trigger="artifact_or_objective",
        report_schema=_REPORT,
        stages=(linked,),
    )

    document = export_loop(loop, {"implement": source})
    exported = document["stages"][0]
    # The body is the effective (merged) stage — usable when hand-inspected.
    assert exported["name"] == "Apply lint & typing fixes"
    assert exported["linked_from"] == "implement@6105f3"

    imported = parse_loop_import(document).stages[0]
    assert imported.stage.name == "Apply lint & typing fixes"

    # Diffing the imported effective body against the local base recovers the
    # loop-local overrides the exporter had applied.
    drift = overrides_from_drift(imported.stage, base)
    assert drift is not None
    assert drift.name == "Apply lint & typing fixes"
    assert drift.instructions == "Fix only the reported lints."


def test_no_drift_when_effective_equals_base() -> None:
    base = LoopStepDefinition(
        step_id="checks",
        name="Checks",
        kind=LoopStepKind.DETERMINISTIC_CHECK,
        check_adapter="command",
        check_command=("pytest",),
    )
    # Same content but with loop wiring (which is not an override field).
    effective = replace(base, transitions={LoopOutcome.PASS: "approval"})
    assert overrides_from_drift(effective, base) is None


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "kind": "loop", "name": "x", "stages": [{}]},
        {"schema_version": 1, "kind": "stage", "name": "x", "stages": [{}]},
        {"schema_version": 1, "kind": "loop", "name": "x", "stages": []},
        "not a mapping",
    ],
)
def test_parse_rejects_malformed_loop_documents(document: object) -> None:
    with pytest.raises(TransportInvalid):
        parse_loop_import(document)


def test_parse_rejects_bad_linked_from() -> None:
    document = {
        "schema_version": 1,
        "kind": "loop",
        "name": "Broken",
        "stages": [
            {"id": "s", "name": "S", "kind": "agent_task", "linked_from": "no-at-sign"}
        ],
    }
    with pytest.raises(TransportInvalid):
        parse_loop_import(document)
