"""GET /api/runs — the cross-work recent-runs listing.

Full path on a real SQLite database: repository ordering, the limit
clamp, and the provenance fields a search row renders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.domain.loop.dtos import LoopStatus, LoopTargetKind
from src.domain.loop.models import LoopRunRecord

BASE = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _store_run(
    client: TestClient,
    run_id: str,
    work_slug: str,
    *,
    minutes: int,
    artifact_id: str | None = None,
    number: int = 1,
    workspace: str = "",
) -> None:
    # Keys are namespaced by target the way the runner writes them, so a
    # run id round-trips through ``loop_run_key`` in the route.
    record = LoopRunRecord(
        run_key=(
            f"loop-{artifact_id}-{run_id}" if artifact_id else f"loop-objective-{run_id}"
        ),
        work_slug=work_slug,
        target_kind=(
            LoopTargetKind.PLANNING_ARTIFACT
            if artifact_id
            else LoopTargetKind.OBJECTIVE
        ),
        target_ref=f"goal for {run_id}",
        artifact_id=artifact_id,
        plan_run_id=run_id,
        definition_id="atelier-fast",
        definition_revision="rev-1",
        definition_snapshot={},
        status=LoopStatus.RUNNING,
        current_step_id=None,
        state={"number": number, "workspace_path": workspace},
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


# ---------------------------------------------------------------------------
# REST: open a run's workspace in a terminal — run-scoped, so it lands where
# the editor does and works on stages that never had an agent
# ---------------------------------------------------------------------------


def test_open_run_in_console_uses_the_runs_own_workspace(
    works: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.application.http.routes import works as works_module

    captured: dict[str, object] = {}

    def fake_open(path: str, kind: str = "system") -> None:
        captured["path"] = path
        captured["kind"] = kind

    monkeypatch.setattr(works_module, "open_in_terminal", fake_open)
    workspace = tmp_path / "run-workspace"
    workspace.mkdir()
    _store_run(works, "run-a", "WRK-001", minutes=1, workspace=str(workspace))

    response = works.post(
        "/api/works/WRK-001/runs/run-a/open-in-console?kind=iterm2"
    )

    assert response.status_code == 204, response.text
    assert captured["path"] == str(workspace)
    assert captured["kind"] == "iterm2"


def test_open_run_in_console_404s_for_an_unknown_run(works: TestClient) -> None:
    response = works.post("/api/works/WRK-001/runs/run-404/open-in-console")

    assert response.status_code == 404


def test_open_run_in_console_reports_a_launcher_that_will_not_start(
    works: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.application.http.routes import works as works_module

    def raising(path: str, kind: str = "system") -> None:
        raise OSError("no terminal")

    monkeypatch.setattr(works_module, "open_in_terminal", raising)
    _store_run(works, "run-a", "WRK-001", minutes=1, workspace=str(tmp_path))

    response = works.post("/api/works/WRK-001/runs/run-a/open-in-console")

    assert response.status_code == 500
    assert "open in console failed" in response.json()["detail"]
