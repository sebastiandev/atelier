"""GET /api/runs — the cross-work recent-runs listing.

Full path on a real SQLite database: repository ordering, the limit
clamp, and the provenance fields a search row renders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.domain.loop.dtos import LoopStatus, LoopTargetKind
from src.domain.loop.models import LoopRunRecord

BASE = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _store_run(
    client: TestClient,
    run_key: str,
    work_slug: str,
    *,
    minutes: int,
    artifact_id: str | None = None,
    number: int = 1,
) -> None:
    record = LoopRunRecord(
        run_key=run_key,
        work_slug=work_slug,
        target_kind=(
            LoopTargetKind.PLANNING_ARTIFACT
            if artifact_id
            else LoopTargetKind.OBJECTIVE
        ),
        target_ref=f"goal for {run_key}",
        artifact_id=artifact_id,
        plan_run_id=f"run-{run_key}" if artifact_id else None,
        definition_id="atelier-fast",
        definition_revision="rev-1",
        definition_snapshot={},
        status=LoopStatus.RUNNING,
        current_step_id=None,
        state={"number": number},
        started_at=BASE,
        updated_at=BASE + timedelta(minutes=minutes),
    )
    client.app.state.loop_runs.upsert(record, ())


@pytest.fixture
def works(app_client: TestClient) -> TestClient:
    for slug, name in (("WRK-001", "Auth rollout"), ("WRK-002", "Billing")):
        response = app_client.post(
            "/api/works", json={"name": name, "description": ""}
        )
        assert response.status_code in (200, 201), response.text
        assert response.json()["slug"] == slug
    return app_client


def test_runs_are_listed_newest_updated_first(works: TestClient) -> None:
    _store_run(works, "a", "WRK-001", minutes=1)
    _store_run(works, "b", "WRK-002", minutes=9)
    _store_run(works, "c", "WRK-001", minutes=5)

    body = works.get("/api/runs").json()

    assert [row["goal"] for row in body] == [
        "goal for b",
        "goal for c",
        "goal for a",
    ]


def test_a_run_carries_its_work_name(works: TestClient) -> None:
    _store_run(works, "a", "WRK-002", minutes=1)

    row = works.get("/api/runs").json()[0]

    assert row["work_slug"] == "WRK-002"
    assert row["work_name"] == "Billing"


def test_a_loop_mode_run_reports_no_source(works: TestClient) -> None:
    _store_run(works, "a", "WRK-001", minutes=1)

    row = works.get("/api/runs").json()[0]

    assert row["source_kind"] is None
    assert row["source_ref"] is None
    assert row["source_title"] is None


def test_a_story_triggered_run_reports_its_story(works: TestClient) -> None:
    _store_run(works, "a", "WRK-001", minutes=1, artifact_id="story-001")

    row = works.get("/api/runs").json()[0]

    assert row["source_kind"] == "story"
    assert row["source_ref"] == "story-001"
    # No plan file exists for this work, so the title degrades to null
    # rather than failing the request.
    assert row["source_title"] is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [("", 3), ("?limit=2", 2), ("?limit=0", 1), ("?limit=999", 3)],
)
def test_limit_is_honoured_and_clamped(
    works: TestClient, query: str, expected: int
) -> None:
    for i in range(3):
        _store_run(works, f"r{i}", "WRK-001", minutes=i)

    assert len(works.get(f"/api/runs{query}").json()) == expected


def test_an_empty_database_returns_an_empty_list(app_client: TestClient) -> None:
    assert app_client.get("/api/runs").json() == []


def test_work_run_response_stays_backward_compatible(works: TestClient) -> None:
    """The per-work listing gains three optional fields, always null here.

    ``store.list_runs`` filters that endpoint to OBJECTIVE runs, so a
    story-triggered run never reaches it -- cross-work ``/api/runs`` is
    the only listing that carries provenance. What matters here is that
    a client ignoring the new fields sees exactly what it saw before.
    """
    _store_run(works, "a", "WRK-001", minutes=1, artifact_id="story-001")
    _store_run(works, "b", "WRK-001", minutes=2)

    rows = works.get("/api/works/WRK-001/runs").json()

    assert [row["goal"] for row in rows] == ["goal for b"]
    assert rows[0]["source_kind"] is None
    assert rows[0]["source_ref"] is None
    assert rows[0]["source_title"] is None
