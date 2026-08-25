"""Recent-runs listing: ordering is the repository's, labels are ours.

The command only decorates rows with the Work name and, for
story-triggered runs, the story title. What it must never do is drop a
row because one plan file is unreadable, or read a plan once per run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from src.domain.commands.runs import recent
from src.domain.loop.dtos import LoopStatus, LoopTargetKind
from src.domain.loop.models import LoopRunRecord

NOW = datetime(2026, 8, 25, tzinfo=UTC)


def _record(
    run_key: str,
    work_slug: str,
    *,
    artifact_id: str | None = None,
) -> LoopRunRecord:
    return LoopRunRecord(
        run_key=run_key,
        work_slug=work_slug,
        target_kind=(
            LoopTargetKind.PLANNING_ARTIFACT
            if artifact_id
            else LoopTargetKind.OBJECTIVE
        ),
        target_ref="ship the thing",
        artifact_id=artifact_id,
        plan_run_id=f"run-{run_key}" if artifact_id else None,
        definition_id="atelier-fast",
        definition_revision="abc123",
        definition_snapshot={},
        status=LoopStatus.RUNNING,
        current_step_id=None,
        state={"number": 1},
        started_at=NOW,
        updated_at=NOW,
    )


class _Runs:
    def __init__(self, records: list[LoopRunRecord]) -> None:
        self._records = records
        self.limits: list[int] = []

    def list_recent(self, limit: int) -> list[LoopRunRecord]:
        self.limits.append(limit)
        return self._records[:limit]


class _Work:
    def __init__(self, slug: str, name: str) -> None:
        self.slug = slug
        self.name = name


class _WorkStore:
    def __init__(self, works: list[_Work]) -> None:
        self._works = works

    def list_works(self) -> list[_Work]:
        return self._works


class _Planning:
    """Stands in for the planning file port; never read directly here."""


def _plans(
    monkeypatch: Any,
    titles: dict[str, dict[str, str]],
    *,
    broken: str = "",
) -> list[str]:
    """Patch the plan read, returning the list that records each read.

    Patched at ``get_plan`` rather than through a fake file port: the
    command's contract is "one plan read per work, unreadable plans do
    not fail the listing", and that is what this seam expresses.
    """
    reads: list[str] = []

    def fake_get_plan(self: Any, work_slug: str) -> Any:
        reads.append(work_slug)
        if work_slug == broken:
            raise OSError("plan file is unreadable")
        if work_slug not in titles:
            return None
        return type(
            "Plan",
            (),
            {
                "artifacts": [
                    type("Artifact", (), {"id": artifact_id, "title": title})()
                    for artifact_id, title in titles[work_slug].items()
                ]
            },
        )()

    monkeypatch.setattr(
        "src.domain.planning.service.PlanningService.get_plan", fake_get_plan
    )
    return reads


def test_story_runs_get_their_story_title(monkeypatch: Any) -> None:
    _plans(monkeypatch, {"WRK-001": {"ST-04": "Password fallback during rollout"}})

    rows = recent.execute(
        _Runs([_record("a", "WRK-001", artifact_id="ST-04")]),
        _WorkStore([_Work("WRK-001", "Auth rollout")]),
        _Planning(),
    )

    assert len(rows) == 1
    assert rows[0].work_name == "Auth rollout"
    assert rows[0].source_title == "Password fallback during rollout"


def test_loop_mode_runs_have_no_story(monkeypatch: Any) -> None:
    reads = _plans(monkeypatch, {})

    rows = recent.execute(
        _Runs([_record("a", "WRK-001")]),
        _WorkStore([_Work("WRK-001", "Auth rollout")]),
        _Planning(),
    )

    assert rows[0].source_title is None
    # No story-triggered run in the batch, so no plan is read at all.
    assert reads == []


def test_an_unreadable_plan_still_lists_its_runs(monkeypatch: Any) -> None:
    _plans(monkeypatch, {}, broken="WRK-001")

    rows = recent.execute(
        _Runs([_record("a", "WRK-001", artifact_id="ST-04")]),
        _WorkStore([_Work("WRK-001", "Auth rollout")]),
        _Planning(),
    )

    assert len(rows) == 1
    assert rows[0].source_title is None
    assert rows[0].record.source is not None


def test_a_plan_is_read_once_per_work_not_once_per_run(monkeypatch: Any) -> None:
    reads = _plans(monkeypatch, {"WRK-001": {"ST-04": "One", "ST-05": "Two"}})

    recent.execute(
        _Runs(
            [
                _record("a", "WRK-001", artifact_id="ST-04"),
                _record("b", "WRK-001", artifact_id="ST-05"),
                _record("c", "WRK-001", artifact_id="ST-04"),
            ]
        ),
        _WorkStore([_Work("WRK-001", "Auth rollout")]),
        _Planning(),
    )

    assert reads == ["WRK-001"]


def test_a_run_whose_work_is_gone_still_lists(monkeypatch: Any) -> None:
    _plans(monkeypatch, {})

    rows = recent.execute(
        _Runs([_record("a", "WRK-404")]),
        _WorkStore([]),
        _Planning(),
    )

    assert len(rows) == 1
    assert rows[0].work_name == ""


def test_limit_is_passed_through_to_the_repository(monkeypatch: Any) -> None:
    _plans(monkeypatch, {})
    runs = _Runs([_record(str(i), "WRK-001") for i in range(10)])

    rows = recent.execute(runs, _WorkStore([]), _Planning(), limit=3)

    assert runs.limits == [3]
    assert len(rows) == 3


def test_an_empty_repository_reads_no_plans(monkeypatch: Any) -> None:
    reads = _plans(monkeypatch, {})

    assert recent.execute(_Runs([]), _WorkStore([]), _Planning()) == ()
    assert reads == []


@pytest.mark.parametrize(
    ("asked", "reaches_sql"),
    [(999_999, 200), (201, 200), (50, 50), (1, 1), (0, 1), (-5, 1)],
)
def test_the_limit_is_clamped_before_it_reaches_sql(
    monkeypatch: Any, asked: int, reaches_sql: int
) -> None:
    """A caller cannot ask the listing for the whole table."""
    _plans(monkeypatch, {})
    runs = _Runs([])

    recent.execute(runs, _WorkStore([]), _Planning(), limit=asked)

    assert runs.limits == [reaches_sql]
