"""Full-path tests for repository-owned loop definition routes."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from src.application.http.routes import loops as loop_routes
from src.domain.planning.models import PlanningSession


def _create_work_with_planning_root(client: TestClient, root: Path) -> None:
    root.mkdir(parents=True)
    response = client.post(
        "/api/works",
        json={"name": "Loops", "description": "Edit reusable loops."},
    )
    assert response.status_code == 201, response.text
    now = datetime.now(UTC)
    client.app.state.planning_sessions.upsert_session(
        PlanningSession(
            work_slug="WRK-001",
            planning_chat_slug=None,
            root_path=str(root),
            artifact_root_path="_bmad-output/WRK-001",
            framework="bmad",
            profile="feature",
            provider="amp",
            model="default",
            options={},
            created_at=now,
            updated_at=now,
        )
    )


def test_definition_fork_update_conflict_and_delete(
    app_client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    _create_work_with_planning_root(app_client, root)

    listed = app_client.get("/api/works/WRK-001/loop-definitions")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [
        "atelier-fast",
        "atelier-reviewed",
        "atelier-secure",
    ]

    forked = app_client.post(
        "/api/works/WRK-001/loop-definitions/atelier-reviewed/fork",
        json={"id": "reviewed-platform", "name": "Reviewed Platform"},
    )
    assert forked.status_code == 201, forked.text
    body = forked.json()
    assert body["scope"] == "repo"
    assert body["forked_from"] == "atelier-reviewed"
    assert body["valid"] is True
    definition_dir = root / ".atelier" / "loops" / "reviewed-platform"
    assert (definition_dir / "loop.yaml").exists()
    assert (definition_dir / "steps" / "code-review.md").exists()

    original_revision = body["revision"]
    body["description"] = "Review against repository ADRs."
    updated = app_client.put(
        "/api/works/WRK-001/loop-definitions/reviewed-platform",
        json={
            "id": body["id"],
            "name": body["name"],
            "description": body["description"],
            "expected_revision": original_revision,
            "forked_from": body["forked_from"],
            "stages": body["stages"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] != original_revision

    stale = app_client.put(
        "/api/works/WRK-001/loop-definitions/reviewed-platform",
        json={
            "id": body["id"],
            "name": body["name"],
            "description": "Stale overwrite",
            "expected_revision": original_revision,
            "forked_from": body["forked_from"],
            "stages": body["stages"],
        },
    )
    assert stale.status_code == 409, stale.text

    deleted = app_client.delete(
        "/api/works/WRK-001/loop-definitions/reviewed-platform"
    )
    assert deleted.status_code == 204, deleted.text
    assert not definition_dir.exists()


def test_builtin_id_cannot_be_replaced(app_client: TestClient, tmp_path: Path) -> None:
    _create_work_with_planning_root(app_client, tmp_path / "repo")
    builtin = app_client.get(
        "/api/works/WRK-001/loop-definitions/atelier-fast"
    ).json()

    response = app_client.post(
        "/api/works/WRK-001/loop-definitions",
        json={
            "id": builtin["id"],
            "name": builtin["name"],
            "description": builtin["description"],
            "stages": builtin["stages"],
        },
    )

    assert response.status_code == 422, response.text


def test_reveal_saved_repository_loop(
    app_client: TestClient,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "repo"
    _create_work_with_planning_root(app_client, root)
    forked = app_client.post(
        "/api/works/WRK-001/loop-definitions/atelier-fast/fork",
        json={"id": "fast-repo", "name": "Fast repo"},
    )
    assert forked.status_code == 201, forked.text
    revealed: list[str] = []
    monkeypatch.setattr(loop_routes, "open_in_file_browser", revealed.append)

    response = app_client.post(
        "/api/works/WRK-001/loop-definitions/fast-repo/reveal"
    )

    assert response.status_code == 204, response.text
    assert revealed == [str(root / ".atelier" / "loops" / "fast-repo")]
