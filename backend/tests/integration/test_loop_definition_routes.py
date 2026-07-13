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


def test_repository_rejects_dot_definition_ids(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    loops = root / ".atelier" / "loops"
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
    payload["scope"] = "repo"
    payload["stages"][0]["agent"]["provider"] = "unknown"

    response = app_client.post("/api/loops", json=payload)

    assert response.status_code == 422, response.text
    assert "unknown provider" in response.json()["detail"]


def test_canonical_loop_verbs_do_not_upsert(app_client: TestClient) -> None:
    payload = app_client.get("/api/loops/atelier-fast").json()
    payload.update(id="verb-loop", name="Verb loop", scope="repo")
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


def test_canonical_library_and_work_overlay_crud(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    created_work = app_client.post(
        "/api/works",
        json={"name": "Scoped loops", "description": ""},
    )
    assert created_work.status_code == 201, created_work.text
    builtin = app_client.get("/api/loops/atelier-fast").json()
    library_payload = {
        "id": "shared-fast",
        "name": "Shared fast",
        "description": "Reusable default.",
        "scope": "repo",
        "forked_from": "atelier-fast",
        "stages": builtin["stages"],
    }

    created_library = app_client.post("/api/loops", json=library_payload)
    assert created_library.status_code == 201, created_library.text
    library = created_library.json()
    assert library["scope"] == "repo"
    assert (
        test_settings.workspace_root
        / ".atelier"
        / "loops"
        / "shared-fast"
        / "loop.yaml"
    ).exists()

    updated_library = app_client.put(
        "/api/loops/shared-fast",
        json={
            **library_payload,
            "description": "Updated reusable default.",
            "expected_revision": library["revision"],
        },
    )
    assert updated_library.status_code == 200, updated_library.text

    work_stages = updated_library.json()["stages"]
    work_stages[0]["agent"]["permissions"] = None
    created_work_loop = app_client.post(
        "/api/loops",
        json={
            **library_payload,
            "name": "Work fast",
            "scope": "work",
            "work_slug": "WRK-001",
            "stages": work_stages,
        },
    )
    assert created_work_loop.status_code == 201, created_work_loop.text
    work_loop = created_work_loop.json()
    assert work_loop["scope"] == "work"
    assert work_loop["stages"][0]["agent"]["permissions"] is None
    work_yaml = (
        test_settings.workspace_root
        / "works"
        / "WRK-001"
        / ".atelier"
        / "loops"
        / "shared-fast"
        / "loop.yaml"
    )
    assert "permissions: inherit" in work_yaml.read_text()

    patched = app_client.patch(
        "/api/loops/shared-fast",
        json={
            **library_payload,
            "name": "Work fast patched",
            "scope": "work",
            "work_slug": "WRK-001",
            "stages": work_loop["stages"],
            "expected_revision": work_loop["revision"],
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Work fast patched"
    assert app_client.get(
        "/api/loops/shared-fast?work_slug=WRK-001"
    ).json()["scope"] == "work"
    assert app_client.get("/api/loops/shared-fast").json()["scope"] == "repo"

    deleted = app_client.delete(
        "/api/loops/shared-fast?work_slug=WRK-001&scope=work"
    )
    assert deleted.status_code == 204, deleted.text
    fallback = app_client.get("/api/loops/shared-fast?work_slug=WRK-001")
    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["scope"] == "repo"


def test_canonical_routes_manage_legacy_repository_loop(
    app_client: TestClient,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "repo"
    _create_work_with_planning_root(app_client, root)
    forked = app_client.post(
        "/api/works/WRK-001/loop-definitions/atelier-fast/fork",
        json={"id": "legacy-fast", "name": "Legacy fast"},
    )
    assert forked.status_code == 201, forked.text

    fetched = app_client.get(
        "/api/loops/legacy-fast?work_slug=WRK-001&scope=repo"
    )
    assert fetched.status_code == 200, fetched.text
    legacy = fetched.json()

    updated = app_client.put(
        "/api/loops/legacy-fast",
        json={
            "id": legacy["id"],
            "name": legacy["name"],
            "description": "Updated through the canonical API.",
            "scope": "repo",
            "work_slug": "WRK-001",
            "expected_revision": legacy["revision"],
            "forked_from": legacy["forked_from"],
            "stages": legacy["stages"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert "Updated through the canonical API." in (
        root / ".atelier" / "loops" / "legacy-fast" / "loop.yaml"
    ).read_text()

    revealed: list[str] = []
    monkeypatch.setattr(loop_routes, "open_in_file_browser", revealed.append)
    reveal = app_client.post(
        "/api/loops/legacy-fast/reveal?work_slug=WRK-001&scope=repo"
    )
    assert reveal.status_code == 204, reveal.text
    assert revealed == [str(root / ".atelier" / "loops" / "legacy-fast")]

    deleted = app_client.delete(
        "/api/loops/legacy-fast?work_slug=WRK-001&scope=repo"
    )
    assert deleted.status_code == 204, deleted.text
    assert not (root / ".atelier" / "loops" / "legacy-fast").exists()


def test_canonical_repository_scope_prefers_library_over_legacy(
    app_client: TestClient,
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _create_work_with_planning_root(app_client, root)
    legacy_response = app_client.post(
        "/api/works/WRK-001/loop-definitions/atelier-fast/fork",
        json={"id": "shared-id", "name": "Legacy copy"},
    )
    assert legacy_response.status_code == 201, legacy_response.text
    legacy = legacy_response.json()
    library_response = app_client.post(
        "/api/loops",
        json={
            "id": "shared-id",
            "name": "Library copy",
            "description": "Global reusable loop.",
            "scope": "repo",
            "forked_from": "atelier-fast",
            "stages": legacy["stages"],
        },
    )
    assert library_response.status_code == 201, library_response.text
    library = library_response.json()

    fetched = app_client.get(
        "/api/loops/shared-id?work_slug=WRK-001&scope=repo"
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["name"] == "Library copy"

    updated = app_client.put(
        "/api/loops/shared-id",
        json={
            "id": "shared-id",
            "name": "Updated library copy",
            "description": library["description"],
            "scope": "repo",
            "work_slug": "WRK-001",
            "expected_revision": library["revision"],
            "forked_from": library["forked_from"],
            "stages": library["stages"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert "Updated library copy" in (
        test_settings.workspace_root
        / ".atelier"
        / "loops"
        / "shared-id"
        / "loop.yaml"
    ).read_text()
    assert "Legacy copy" in (
        root / ".atelier" / "loops" / "shared-id" / "loop.yaml"
    ).read_text()

    deleted = app_client.delete(
        "/api/loops/shared-id?work_slug=WRK-001&scope=repo"
    )
    assert deleted.status_code == 204, deleted.text
    fallback = app_client.get(
        "/api/loops/shared-id?work_slug=WRK-001&scope=repo"
    )
    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["name"] == "Legacy copy"
