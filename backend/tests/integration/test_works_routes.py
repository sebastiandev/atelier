"""Integration tests for /api/works via TestClient.

Exercises the full vertical slice — pydantic schema → router → command →
WorkStoreService → SQL adapter + FS adapter. The fixture wires the real
app via `app_client`; assertions reach into both the response body and
the filesystem to confirm both sides of the boundary.
"""

import json

import pytest
from fastapi.testclient import TestClient

from src.settings import Settings


def _new_work(name: str = "Migration") -> dict[str, object]:
    return {
        "name": name,
        "description": f"brief for {name}",
        "contexts": [],
    }


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_post_creates_work_and_returns_detail(
    app_client: TestClient, test_settings: Settings
) -> None:
    response = app_client.post("/api/works", json=_new_work())
    assert response.status_code == 201
    body = response.json()

    assert body["slug"] == "WRK-001"
    assert body["name"] == "Migration"
    assert body["status"] == "active"
    assert body["contexts"] == []
    # ``folder`` is now per-agent; the response should not carry it on a
    # Work. ``atelier_path`` is the new pill the FE shows in the header.
    assert "folder" not in body
    work_dir = test_settings.workspace_root / "works" / "WRK-001"
    assert body["atelier_path"] == str(work_dir)
    assert (work_dir / "work.json").exists()
    assert (work_dir / "brief.md").exists()


def test_post_with_contexts_persists_them(app_client: TestClient, test_settings: Settings) -> None:
    payload = _new_work()
    payload["contexts"] = [
        {"type": "jira", "value": "FOO-123", "conn_id": "con-1"},
        {"type": "url", "value": "https://example.test/x"},
    ]
    response = app_client.post("/api/works", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert len(body["contexts"]) == 2
    assert body["contexts"][0] == {
        "type": "jira",
        "value": "FOO-123",
        "conn_id": "con-1",
    }

    work_json = json.loads(
        (test_settings.workspace_root / "works" / "WRK-001" / "work.json").read_text()
    )
    assert work_json["contexts"] == payload["contexts"]


def test_post_rejects_empty_name(app_client: TestClient) -> None:
    payload = _new_work()
    payload["name"] = ""
    response = app_client.post("/api/works", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_returns_summaries_for_all_works(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work(name="A"))
    app_client.post("/api/works", json=_new_work(name="B"))
    response = app_client.get("/api/works")
    assert response.status_code == 200
    body = response.json()
    assert {w["slug"] for w in body} == {"WRK-001", "WRK-002"}
    # Summary doesn't include contexts.
    assert "contexts" not in body[0]


def test_list_excludes_deleted_works(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work(name="A"))
    app_client.post("/api/works", json=_new_work(name="B"))
    app_client.delete("/api/works/WRK-001")

    body = app_client.get("/api/works").json()
    assert {w["slug"] for w in body} == {"WRK-002"}


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------


def test_get_returns_detail_with_contexts(app_client: TestClient) -> None:
    payload = _new_work()
    payload["contexts"] = [{"type": "text", "value": "see deck"}]
    app_client.post("/api/works", json=payload)

    response = app_client.get("/api/works/WRK-001")
    assert response.status_code == 200
    body = response.json()
    assert body["contexts"] == [{"type": "text", "value": "see deck", "conn_id": None}]


def test_get_returns_404_for_unknown(app_client: TestClient) -> None:
    assert app_client.get("/api/works/WRK-404").status_code == 404


def test_get_returns_404_for_deleted(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())
    app_client.delete("/api/works/WRK-001")
    assert app_client.get("/api/works/WRK-001").status_code == 404


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


def test_patch_renames_work(app_client: TestClient, test_settings: Settings) -> None:
    app_client.post("/api/works", json=_new_work(name="Old"))
    response = app_client.patch("/api/works/WRK-001", json={"name": "New"})
    assert response.status_code == 200
    assert response.json()["name"] == "New"

    work_json = json.loads(
        (test_settings.workspace_root / "works" / "WRK-001" / "work.json").read_text()
    )
    assert work_json["name"] == "New"


def test_patch_description_rewrites_brief_md(
    app_client: TestClient, test_settings: Settings
) -> None:
    app_client.post("/api/works", json=_new_work(name="W"))
    app_client.patch("/api/works/WRK-001", json={"description": "## new brief\n\nbody"})
    brief = (test_settings.workspace_root / "works" / "WRK-001" / "brief.md").read_text()
    assert brief == "## new brief\n\nbody"


def test_patch_status_to_completed(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())
    response = app_client.patch("/api/works/WRK-001", json={"status": "completed"})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_patch_replaces_contexts(app_client: TestClient) -> None:
    payload = _new_work()
    payload["contexts"] = [{"type": "text", "value": "old"}]
    app_client.post("/api/works", json=payload)
    response = app_client.patch(
        "/api/works/WRK-001",
        json={"contexts": [{"type": "url", "value": "https://example.test/new"}]},
    )
    assert response.status_code == 200
    assert response.json()["contexts"] == [
        {"type": "url", "value": "https://example.test/new", "conn_id": None}
    ]


def test_patch_returns_404_for_unknown(app_client: TestClient) -> None:
    assert app_client.patch("/api/works/WRK-404", json={"name": "x"}).status_code == 404


def test_patch_404_for_deleted(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())
    app_client.delete("/api/works/WRK-001")
    assert app_client.patch("/api/works/WRK-001", json={"name": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# Delete (permanent)
# ---------------------------------------------------------------------------


def test_delete_returns_204(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())
    response = app_client.delete("/api/works/WRK-001")
    assert response.status_code == 204
    assert response.content == b""


def test_delete_removes_filesystem(app_client: TestClient, test_settings: Settings) -> None:
    app_client.post("/api/works", json=_new_work())
    work_dir = test_settings.workspace_root / "works" / "WRK-001"
    assert work_dir.exists()

    app_client.delete("/api/works/WRK-001")

    assert not work_dir.exists()


def test_delete_removes_work_record(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())
    app_client.delete("/api/works/WRK-001")

    assert app_client.get("/api/works/WRK-001").status_code == 404


def test_delete_returns_404_for_unknown(app_client: TestClient) -> None:
    assert app_client.delete("/api/works/WRK-404").status_code == 404


# ---------------------------------------------------------------------------
# Reconcile + restart preserves soft-delete
# ---------------------------------------------------------------------------


def test_soft_delete_survives_app_restart(app_client: TestClient, test_settings: Settings) -> None:
    """Restart the app on the same workspace; reconcile reads work.json and
    the deleted status persists."""
    app_client.post("/api/works", json=_new_work())
    app_client.app.state.workstore.soft_delete_work("WRK-001")

    # Boot a fresh app on the same on-disk workspace.
    from src.main import create_app

    fresh = create_app(test_settings)
    with TestClient(fresh) as fresh_client:
        # GET 404 — deleted is hidden post-restart.
        assert fresh_client.get("/api/works/WRK-001").status_code == 404
        # List excludes it too.
        assert fresh_client.get("/api/works").json() == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_patch_with_no_fields_is_noop(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work(name="X"))
    response = app_client.patch("/api/works/WRK-001", json={})
    assert response.status_code == 200
    assert response.json()["name"] == "X"


@pytest.mark.parametrize("count", [1, 5])
def test_create_then_list(app_client: TestClient, count: int) -> None:
    for i in range(count):
        app_client.post("/api/works", json=_new_work(name=f"W{i}"))
    body = app_client.get("/api/works").json()
    assert len(body) == count


def test_empty_workspace_lists_empty(app_client: TestClient) -> None:
    assert app_client.get("/api/works").json() == []


# ---------------------------------------------------------------------------
# Reveal — file-browser shell-out
# ---------------------------------------------------------------------------


def test_reveal_returns_204_and_runs_open_command(
    app_client: TestClient, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reveal shells out to the OS file browser. We monkeypatch
    ``subprocess.run`` so the test doesn't actually pop a Finder window."""
    import subprocess as sp

    from src.application.http.routes import works as works_module

    captured: dict[str, object] = {}

    def fake_run(args: list[str], check: bool = False) -> sp.CompletedProcess[bytes]:
        captured["args"] = args
        captured["check"] = check
        return sp.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(works_module.subprocess, "run", fake_run)

    app_client.post("/api/works", json=_new_work())
    response = app_client.post("/api/works/WRK-001/reveal")
    assert response.status_code == 204

    assert isinstance(captured["args"], list)
    assert str(test_settings.workspace_root / "works" / "WRK-001") in captured["args"]


def test_reveal_404_for_unknown_work(app_client: TestClient) -> None:
    assert app_client.post("/api/works/WRK-404/reveal").status_code == 404


# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------


def test_complete_flips_status_and_returns_agent_count(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())

    res = app_client.post("/api/works/WRK-001/complete")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["work_slug"] == "WRK-001"
    assert body["agent_count"] == 0  # no agents on a freshly-created work

    detail = app_client.get("/api/works/WRK-001").json()
    assert detail["status"] == "completed"


def test_complete_returns_404_for_unknown_work(app_client: TestClient) -> None:
    assert app_client.post("/api/works/WRK-404/complete").status_code == 404


def test_complete_returns_409_when_already_completed(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())
    assert app_client.post("/api/works/WRK-001/complete").status_code == 200
    assert app_client.post("/api/works/WRK-001/complete").status_code == 409


# ---------------------------------------------------------------------------
# Move to project
# ---------------------------------------------------------------------------


def _new_project(name: str = "Atelier", glyph: str = "AT") -> dict[str, object]:
    return {"name": name, "description": "", "glyph": glyph, "color": 250}


def test_move_reparents_work_to_target_project(app_client: TestClient) -> None:
    app_client.post("/api/projects", json=_new_project())
    app_client.post("/api/projects", json=_new_project(name="Side", glyph="SI"))
    app_client.post("/api/works", json={**_new_work(), "project_slug": "PRJ-002"})

    res = app_client.post(
        "/api/works/WRK-001/project", json={"project_slug": "PRJ-001"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["project_slug"] == "PRJ-001"

    # GET reflects the move.
    assert app_client.get("/api/works/WRK-001").json()["project_slug"] == "PRJ-001"


def test_move_to_loose_sets_project_slug_null(app_client: TestClient) -> None:
    app_client.post("/api/projects", json=_new_project())
    app_client.post("/api/works", json={**_new_work(), "project_slug": "PRJ-001"})

    res = app_client.post("/api/works/WRK-001/project", json={"project_slug": None})
    assert res.status_code == 200
    assert res.json()["project_slug"] is None


def test_move_returns_404_for_unknown_work(app_client: TestClient) -> None:
    app_client.post("/api/projects", json=_new_project())
    res = app_client.post(
        "/api/works/WRK-999/project", json={"project_slug": "PRJ-001"}
    )
    assert res.status_code == 404


def test_move_returns_422_for_unknown_target_project(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())
    res = app_client.post(
        "/api/works/WRK-001/project", json={"project_slug": "PRJ-404"}
    )
    assert res.status_code == 422


def test_create_response_includes_iso_timestamp(app_client: TestClient) -> None:
    body = app_client.post("/api/works", json=_new_work()).json()
    # Pydantic serializes datetime to ISO-8601 string.
    assert "T" in body["created_at"]
    assert body["created_at"].endswith(("Z", "+00:00")) or "+" in body["created_at"]


def test_reconcile_keeps_deleted_state_in_db_after_restart(
    app_client: TestClient, test_settings: Settings
) -> None:
    """Verifies that reconcile reads status='deleted' from work.json on
    startup and the SQLite row reflects it (so the public API stays
    consistent across restarts)."""
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session

    from src.domain.models import Work
    from src.infrastructure.database import (
        configure_mappings,
        create_database_engine,
        initialize_database,
    )
    from src.main import create_app

    app_client.post("/api/works", json=_new_work())
    app_client.app.state.workstore.soft_delete_work("WRK-001")

    # Wipe the DB row to force reconcile to re-derive from FS on next startup.
    engine = create_database_engine(test_settings)
    configure_mappings()
    initialize_database(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM works"))
    engine.dispose()

    fresh = create_app(test_settings)
    with TestClient(fresh):
        # Re-read via raw session to inspect the DB row directly.
        engine2 = create_database_engine(test_settings)
        with Session(engine2) as session:
            work = session.execute(select(Work)).scalar_one()
            assert work.status == "deleted"
        engine2.dispose()


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def test_list_artifacts_returns_empty_for_new_work(app_client: TestClient) -> None:
    app_client.post("/api/works", json=_new_work())
    res = app_client.get("/api/works/WRK-001/artifacts")
    assert res.status_code == 200
    assert res.json() == []


def test_list_artifacts_returns_recorded_rows(app_client: TestClient) -> None:
    """Recording via the workstore (the path the supervisor takes) shows up
    in the list endpoint, ordered by creation time."""
    from src.domain.workstore.dtos import RecordArtifactRequest

    app_client.post("/api/works", json=_new_work())
    workstore = app_client.app.state.workstore

    workstore.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            type="pr",
            title="Add foo",
            status="open",
            url="https://github.com/x/y/pull/1",
            repo="x/y",
        )
    )
    workstore.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            type="jira",
            title="Implement bar",
            status="in_progress",
            url="https://j.example/X-7",
        )
    )

    res = app_client.get("/api/works/WRK-001/artifacts")
    assert res.status_code == 200
    body = res.json()
    assert [a["type"] for a in body] == ["pr", "jira"]
    assert body[0]["slug"] == "art-1"
    assert body[0]["url"] == "https://github.com/x/y/pull/1"
    assert body[0]["repo"] == "x/y"
    assert body[1]["status"] == "in_progress"
    # No agent attribution was supplied — agent_slug is null.
    assert body[0]["agent_slug"] is None


def test_list_artifacts_404_for_unknown_work(app_client: TestClient) -> None:
    assert app_client.get("/api/works/WRK-404/artifacts").status_code == 404
