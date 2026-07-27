"""A run's trigger is provenance, not a second kind of run.

Callers branch on ``source`` so the engine, stores and routes stay
target-agnostic; ``target_kind`` models the same fact as two kinds of run
and is what the unification removes.
"""

from datetime import UTC, datetime

import pytest

from src.domain.loop.dtos import LoopRunSourceKind, LoopStatus, LoopTargetKind
from src.domain.loop.models import LoopRunRecord

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def _record(
    *,
    target_kind: LoopTargetKind,
    artifact_id: str | None,
) -> LoopRunRecord:
    return LoopRunRecord(
        run_key="loop-run-001",
        work_slug="WRK-001",
        target_kind=target_kind,
        target_ref="ship the thing",
        artifact_id=artifact_id,
        plan_run_id="run-001" if artifact_id else None,
        definition_id="atelier-fast",
        definition_revision="abc123",
        definition_snapshot={},
        status=LoopStatus.RUNNING,
        current_step_id="implementation",
        state={},
        started_at=NOW,
        updated_at=NOW,
    )


def test_a_story_triggered_run_reports_its_story() -> None:
    record = _record(
        target_kind=LoopTargetKind.PLANNING_ARTIFACT, artifact_id="story-001"
    )

    assert record.source is not None
    assert record.source.kind is LoopRunSourceKind.STORY
    assert record.source.ref == "story-001"


def test_a_run_started_from_a_goal_has_no_source() -> None:
    record = _record(target_kind=LoopTargetKind.OBJECTIVE, artifact_id=None)

    assert record.source is None


def test_a_story_kind_without_a_reference_has_no_source() -> None:
    """Defensive: an unusable reference must not present as a valid source."""
    record = _record(target_kind=LoopTargetKind.PLANNING_ARTIFACT, artifact_id=None)

    assert record.source is None


@pytest.mark.parametrize("artifact_id", ["story-001", "bug-typebuilder-gaps"])
def test_source_reference_round_trips(artifact_id: str) -> None:
    record = _record(
        target_kind=LoopTargetKind.PLANNING_ARTIFACT, artifact_id=artifact_id
    )

    assert record.source is not None
    assert record.source.ref == artifact_id
