"""Full-path tests for reusable standalone stages and loop links."""

from pathlib import Path

from fastapi.testclient import TestClient

from src.settings import Settings


def test_stage_library_fork_update_and_link_round_trip(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    listed = app_client.get("/api/stages")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [
        "approve",
        "code-review",
        "create-pr",
        "implement",
        "security-review",
        "validate",
    ]

    forked = app_client.post(
        "/api/stages/code-review/fork",
        json={"id": "api-review", "name": "API review"},
    )
    assert forked.status_code == 201, forked.text
    stage = forked.json()
    assert stage["scope"] == "repo"
    assert stage["forked_from"] == "code-review"
    assert stage["stage"]["id"] == "api-review"

    stage["description"] = "Review public API compatibility."
    updated = app_client.put(
        "/api/stages/api-review",
        json={**stage, "expected_revision": stage["revision"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] != stage["revision"]

    loop = app_client.get("/api/loops/atelier-reviewed").json()
    loop.update(id="linked-review", name="Linked review", scope="repo")
    linked = updated.json()["stage"]
    linked.update(
        id="api-review",
        stage_ref={
            "definition_id": "api-review",
            "revision": updated.json()["revision"],
        },
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
    linked_stage = created.json()["stages"][1]
    assert linked_stage["name"] == "API review"
    assert linked_stage["stage_ref"]["definition_id"] == "api-review"
    assert app_client.get("/api/stages/api-review").json()["used_by"] == [
        "linked-review"
    ]

    root = Path(test_settings.workspace_root)
    assert (root / ".atelier" / "stages" / "api-review" / "stage.yaml").exists()
    loop_yaml = (root / ".atelier" / "loops" / "linked-review" / "loop.yaml").read_text()
    assert "from: api-review" in loop_yaml
    assert "from_rev:" in loop_yaml


def test_create_pr_stage_config_is_additive_and_legacy_inline_loops_still_load(
    app_client: TestClient,
) -> None:
    create_pr = app_client.get("/api/stages/create-pr")
    assert create_pr.status_code == 200, create_pr.text
    body = create_pr.json()
    assert body["stage"]["kind"] == "pr"
    assert body["stage"]["pr_config"] == {
        "name_template": "{goal} - {work-id}",
        "description_mode": "automatic",
        "description_instructions": "",
        "manual_body": "",
        "status": "draft",
        "base_branch": "master",
        "branch_name": None,
    }
    assert any(
        item["kind"] == "previous_report" and item["required"]
        for item in body["stage"]["context"]
    )

    legacy = app_client.get("/api/loops/atelier-fast")
    assert legacy.status_code == 200, legacy.text
    assert all(item.get("stage_ref") is None for item in legacy.json()["stages"])
