"""Full-path tests for repository-owned loop definition routes."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.application.http.routes import loops as loop_routes
from src.domain.planning.models import PlanningSession
from src.infrastructure.filesystem.loop_definitions import FsLoopDefinitionRepository
from src.settings import Settings


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
            plan_artifacts_dir="_bmad-output/WRK-001",
            framework="bmad",
            profile="feature",
            provider="amp",
            model="default",
            options={},
            created_at=now,
            updated_at=now,
        )
    )


def test_repository_rejects_dot_definition_ids(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    loops = root / "loops"
    loops.mkdir(parents=True)
    repository = FsLoopDefinitionRepository()

    with pytest.raises(ValueError, match="invalid loop definition id"):
        repository.delete_definition(str(root), ".")

    assert loops.is_dir()


def test_canonical_create_rejects_unknown_stage_provider(
    app_client: TestClient,
) -> None:
    payload = app_client.get("/api/loops/atelier-fast").json()
    payload["id"] = "invalid-provider"
    payload["scope"] = "library"
    payload["stages"][0]["agent"]["provider"] = "unknown"

    response = app_client.post("/api/loops", json=payload)

    assert response.status_code == 422, response.text
    assert "unknown provider" in response.json()["detail"]


def test_command_prefixes_round_trip_through_loop_library(
    app_client: TestClient,
) -> None:
    payload = app_client.get("/api/loops/atelier-reviewed").json()
    payload.update(id="approved-tests", name="Approved tests", scope="library")
    payload["stages"][0]["agent"]["approved_command_prefixes"] = [
        "dt sh -s app-endpoints"
    ]

    created = app_client.post("/api/loops", json=payload)

    assert created.status_code == 201, created.text
    assert created.json()["stages"][0]["agent"]["approved_command_prefixes"] == [
        "dt sh -s app-endpoints"
    ]


def test_fast_mode_round_trips_through_loop_library(
    app_client: TestClient,
) -> None:
    payload = app_client.get("/api/loops/atelier-reviewed").json()
    payload.update(id="fast-review", name="Fast review", scope="library")
    payload["stages"][0]["agent"].update(
        provider="codex-acp",
        model="gpt-5.5",
        fast=True,
    )

    created = app_client.post("/api/loops", json=payload)

    assert created.status_code == 201, created.text
    assert created.json()["stages"][0]["agent"]["fast"] is True


def test_canonical_loop_verbs_do_not_upsert(app_client: TestClient) -> None:
    payload = app_client.get("/api/loops/atelier-fast").json()
    payload.update(id="verb-loop", name="Verb loop", scope="library")
    created = app_client.post("/api/loops", json=payload)
    assert created.status_code == 201, created.text

    post_update = app_client.post(
        "/api/loops",
        json={
            **payload,
            "description": "POST must not update.",
            "expected_revision": created.json()["revision"],
        },
    )
    assert post_update.status_code == 422, post_update.text
    assert app_client.get("/api/loops/verb-loop").json()["description"] != (
        "POST must not update."
    )

    for method in ("put", "patch"):
        response = getattr(app_client, method)(
            "/api/loops/missing-loop",
            json={**payload, "id": "missing-loop", "expected_revision": None},
        )
        assert response.status_code == 422, response.text
    assert app_client.get("/api/loops/missing-loop").status_code == 404


def test_definition_fork_update_conflict_and_delete(
    app_client: TestClient, test_settings: Settings
) -> None:
    listed = app_client.get("/api/loops")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [
        "atelier-fast",
        "atelier-reviewed",
        "atelier-secure",
    ]

    forked = app_client.post(
        "/api/loops/atelier-reviewed/fork",
        json={"id": "reviewed-platform", "name": "Reviewed Platform"},
    )
    assert forked.status_code == 201, forked.text
    body = forked.json()
    assert body["scope"] == "library"
    assert body["forked_from"] == "atelier-reviewed"
    assert body["valid"] is True
    definition_dir = test_settings.workspace_root / "loops" / "reviewed-platform"
    assert (definition_dir / "loop.yaml").exists()
    assert not (definition_dir / "steps").exists()  # instructions are inline now

    original_revision = body["revision"]
    body["description"] = "Review against repository ADRs."
    updated = app_client.put(
        "/api/loops/reviewed-platform",
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
        "/api/loops/reviewed-platform",
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

    deleted = app_client.delete("/api/loops/reviewed-platform")
    assert deleted.status_code == 204, deleted.text
    assert not definition_dir.exists()


def test_builtin_id_cannot_be_replaced(app_client: TestClient) -> None:
    builtin = app_client.get("/api/loops/atelier-fast").json()

    response = app_client.post(
        "/api/loops",
        json={
            "id": builtin["id"],
            "name": builtin["name"],
            "description": builtin["description"],
            "scope": "library",
            "stages": builtin["stages"],
        },
    )

    assert response.status_code == 422, response.text


def test_reveal_saved_repository_loop(
    app_client: TestClient,
    test_settings: Settings,
    monkeypatch: Any,
) -> None:
    forked = app_client.post(
        "/api/loops/atelier-fast/fork",
        json={"id": "fast-repo", "name": "Fast repo"},
    )
    assert forked.status_code == 201, forked.text
    revealed: list[str] = []
    monkeypatch.setattr(loop_routes, "open_in_file_browser", revealed.append)

    response = app_client.post("/api/loops/fast-repo/reveal")

    assert response.status_code == 204, response.text
    assert revealed == [str(test_settings.workspace_root / "loops" / "fast-repo")]
