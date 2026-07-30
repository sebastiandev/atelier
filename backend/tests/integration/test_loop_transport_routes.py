"""Full-path tests for loop/stage import-export routes."""

from __future__ import annotations

import yaml  # type: ignore[import-untyped]
from fastapi.testclient import TestClient


def _fork_stage_and_link_into_loop(app_client: TestClient) -> None:
    """Create a library stage and a library loop that links it."""
    forked = app_client.post(
        "/api/stages/code-review/fork",
        json={"name": "API review"},
    )
    assert forked.status_code == 201, forked.text
    stage = forked.json()

    loop = app_client.get("/api/loops/atelier-reviewed").json()
    loop.update(id="linked-review", name="Linked review", scope="library")
    linked = stage["stage"]
    linked.update(
        id="api-review",
        stage_ref={"definition_id": "api-review", "revision": stage["revision"]},
        transitions={
            "pass": "approval",
            "changes_requested": "implementation",
            "blocked_user": "pause",
            "failed": "fail",
        },
    )
    loop["stages"][0]["transitions"]["pass"] = "api-review"
    loop["stages"][1] = linked
    created = app_client.post("/api/loops", json=loop)
    assert created.status_code == 201, created.text


def test_export_builtin_loop_has_no_links(app_client: TestClient) -> None:
    export = app_client.get("/api/loops/atelier-reviewed/export")

    assert export.status_code == 200, export.text
    assert export.json()["filename"] == "atelier-reviewed.loop.yaml"
    document = yaml.safe_load(export.json()["content"])
    assert document["kind"] == "loop"
    assert document["stages"]
    assert all("linked_from" not in stage for stage in document["stages"])


def test_export_loop_flags_only_linked_stages(app_client: TestClient) -> None:
    _fork_stage_and_link_into_loop(app_client)

    export = app_client.get("/api/loops/linked-review/export")

    assert export.status_code == 200, export.text
    document = yaml.safe_load(export.json()["content"])
    linked = next(item for item in document["stages"] if item["id"] == "api-review")
    assert linked["linked_from"].startswith("api-review@")
    assert linked["outcomes"]
    assert "stage_ref" not in linked
    others = [item for item in document["stages"] if item["id"] != "api-review"]
    assert others and all("linked_from" not in item for item in others)


def test_export_standalone_stage(app_client: TestClient) -> None:
    forked = app_client.post(
        "/api/stages/code-review/fork",
        json={"name": "API review"},
    )
    assert forked.status_code == 201, forked.text

    export = app_client.get("/api/stages/api-review/export")

    assert export.status_code == 200, export.text
    assert export.json()["filename"] == "api-review.stage.yaml"
    document = yaml.safe_load(export.json()["content"])
    assert document["kind"] == "stage"
    assert document["id"] == "api-review"
    assert document["outcomes"]
    assert document["stage"]["kind"] == "agent_review"


def test_export_missing_loop_is_404(app_client: TestClient) -> None:
    assert app_client.get("/api/loops/nope/export").status_code == 404


def _exported(app_client: TestClient, loop_id: str) -> str:
    export = app_client.get(f"/api/loops/{loop_id}/export")
    assert export.status_code == 200, export.text
    return export.json()["content"]


def test_import_links_a_same_revision_stage_silently(app_client: TestClient) -> None:
    _fork_stage_and_link_into_loop(app_client)
    content = _exported(app_client, "linked-review")

    preview = app_client.post(
        "/api/loops/import/preview",
        json={"content": content, "name": "Imported review"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["derived_id"] == "imported-review"
    assert body["id_collision"] is False
    linked_plan = next(s for s in body["stages"] if s["linked_id"] == "api-review")
    assert linked_plan["status"] == "link-clean"
    assert linked_plan["local_exists"] is True

    created = app_client.post(
        "/api/loops/import",
        json={"content": content, "name": "Imported review"},
    )
    assert created.status_code == 201, created.text
    linked = next(
        s for s in created.json()["stages"] if s.get("stage_ref")
    )
    assert linked["stage_ref"]["definition_id"] == "api-review"


def test_import_creates_a_missing_linked_stage(app_client: TestClient) -> None:
    _fork_stage_and_link_into_loop(app_client)
    content = _exported(app_client, "linked-review")
    assert app_client.delete("/api/loops/linked-review").status_code == 204
    assert app_client.delete("/api/stages/api-review").status_code == 204

    preview = app_client.post(
        "/api/loops/import/preview",
        json={"content": content, "name": "Rebuilt review"},
    )
    plan = next(s for s in preview.json()["stages"] if s["linked_id"] == "api-review")
    assert plan["status"] == "link-clean"
    assert plan["local_exists"] is False

    created = app_client.post(
        "/api/loops/import",
        json={"content": content, "name": "Rebuilt review"},
    )
    assert created.status_code == 201, created.text
    rebuilt = app_client.get("/api/stages/api-review")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["used_by"] == ["rebuilt-review"]


def test_import_conflict_needs_resolution_then_replaces(app_client: TestClient) -> None:
    _fork_stage_and_link_into_loop(app_client)
    content = _exported(app_client, "linked-review")
    # Drift the local stage so its revision no longer matches the export.
    stage = app_client.get("/api/stages/api-review").json()
    stage["description"] = "Locally edited."
    bumped = app_client.put(
        "/api/stages/api-review",
        json={**stage, "expected_revision": stage["revision"]},
    )
    assert bumped.status_code == 200, bumped.text

    preview = app_client.post(
        "/api/loops/import/preview",
        json={"content": content, "name": "Conflicting review"},
    )
    plan = next(s for s in preview.json()["stages"] if s["linked_id"] == "api-review")
    assert plan["status"] == "link-conflict"
    assert plan["used_by_count"] == 1  # blast radius: linked-review uses it

    unresolved = app_client.post(
        "/api/loops/import",
        json={"content": content, "name": "Conflicting review"},
    )
    assert unresolved.status_code == 422, unresolved.text

    resolved = app_client.post(
        "/api/loops/import",
        json={
            "content": content,
            "name": "Conflicting review",
            "resolutions": [{"stage_id": "api-review", "action": "replace"}],
        },
    )
    assert resolved.status_code == 201, resolved.text
    # Replace bumped the shared stage back to the imported content.
    assert app_client.get("/api/stages/api-review").json()["description"] != (
        "Locally edited."
    )


def test_import_conflict_new_id_keeps_both_stages(app_client: TestClient) -> None:
    _fork_stage_and_link_into_loop(app_client)
    content = _exported(app_client, "linked-review")
    stage = app_client.get("/api/stages/api-review").json()
    stage["description"] = "Locally edited."
    app_client.put(
        "/api/stages/api-review",
        json={**stage, "expected_revision": stage["revision"]},
    )

    created = app_client.post(
        "/api/loops/import",
        json={
            "content": content,
            "name": "Forked review",
            "resolutions": [
                {
                    "stage_id": "api-review",
                    "action": "new",
                    "new_name": "Imported API review",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    assert app_client.get("/api/stages/api-review").status_code == 200
    assert app_client.get("/api/stages/imported-api-review").status_code == 200
    linked = next(s for s in created.json()["stages"] if s.get("stage_ref"))
    assert linked["stage_ref"]["definition_id"] == "imported-api-review"


def test_import_preserves_loop_local_overrides_on_a_linked_stage(
    app_client: TestClient,
) -> None:
    """A linked builtin stage renamed by the loop keeps its override on import,
    instead of reverting to the base stage's name/instructions/context."""
    implement = app_client.get("/api/stages/implement").json()
    loop = app_client.get("/api/loops/atelier-reviewed").json()
    loop.update(id="override-loop", name="Override loop", scope="library")
    first = loop["stages"][0]
    first["stage_ref"] = {
        "definition_id": "implement",
        "revision": implement["revision"],
    }
    first["overrides"] = {
        "name": "Apply lint & typing fixes",
        "instructions": "Fix only the reported lints.",
    }
    created = app_client.post("/api/loops", json=loop)
    assert created.status_code == 201, created.text
    made = created.json()["stages"][0]
    assert made["name"] == "Apply lint & typing fixes"
    assert made["stage_ref"]["definition_id"] == "implement"

    content = _exported(app_client, "override-loop")

    reimported = app_client.post(
        "/api/loops/import",
        json={"content": content, "name": "Reimported override"},
    )
    assert reimported.status_code == 201, reimported.text
    stage = reimported.json()["stages"][0]
    assert stage["name"] == "Apply lint & typing fixes"  # override preserved
    assert stage["instructions"] == "Fix only the reported lints."
    assert stage["stage_ref"]["definition_id"] == "implement"  # still linked


def _library_loop_with_prefix(app_client: TestClient) -> str:
    payload = app_client.get("/api/loops/atelier-reviewed").json()
    payload.update(id="prefixed-loop", name="Prefixed loop", scope="library")
    payload["stages"][0]["agent"]["approved_command_prefixes"] = ["rm -rf"]
    created = app_client.post("/api/loops", json=payload)
    assert created.status_code == 201, created.text
    return _exported(app_client, "prefixed-loop")


def test_import_surfaces_and_requires_prefix_acceptance(
    app_client: TestClient,
) -> None:
    content = _library_loop_with_prefix(app_client)

    preview = app_client.post(
        "/api/loops/import/preview",
        json={"content": content, "name": "Imported prefixed"},
    )
    first = preview.json()["stages"][0]
    assert first["command_prefixes"] == ["rm -rf"]
    assert first["grants_write"] is True

    blocked = app_client.post(
        "/api/loops/import",
        json={"content": content, "name": "Imported prefixed"},
    )
    assert blocked.status_code == 422, blocked.text

    accepted = app_client.post(
        "/api/loops/import",
        json={
            "content": content,
            "name": "Imported prefixed",
            "accepted_command_prefixes": ["rm -rf"],
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["stages"][0]["agent"]["approved_command_prefixes"] == [
        "rm -rf"
    ]


def test_import_loop_id_collision(app_client: TestClient) -> None:
    content = _exported(app_client, "atelier-reviewed")

    preview = app_client.post(
        "/api/loops/import/preview",
        json={"content": content, "name": "atelier reviewed"},
    )
    assert preview.json()["id_collision"] is True

    collided = app_client.post(
        "/api/loops/import",
        json={"content": content, "name": "atelier reviewed"},
    )
    assert collided.status_code == 409, collided.text

    renamed = app_client.post(
        "/api/loops/import",
        json={"content": content, "name": "Fresh import"},
    )
    assert renamed.status_code == 201, renamed.text
    assert renamed.json()["id"] == "fresh-import"


def test_import_stage_round_trip_and_collision(app_client: TestClient) -> None:
    forked = app_client.post(
        "/api/stages/code-review/fork", json={"name": "API review"}
    )
    assert forked.status_code == 201, forked.text
    content = app_client.get("/api/stages/api-review/export").json()["content"]

    # Same-name import collides with the identical local stage → no-op success.
    preview = app_client.post(
        "/api/stages/import/preview",
        json={"content": content, "name": "API review"},
    )
    body = preview.json()
    assert body["id_collision"] is True
    assert body["same_revision"] is True

    same = app_client.post(
        "/api/stages/import",
        json={"content": content, "name": "API review"},
    )
    assert same.status_code == 201, same.text
    assert same.json()["id"] == "api-review"

    # A fresh name imports it as a new stage.
    fresh = app_client.post(
        "/api/stages/import",
        json={"content": content, "name": "Platform review"},
    )
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["id"] == "platform-review"
