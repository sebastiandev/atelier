"""Agents launched from a planning story (STORY-042).

A story agent carries its ``artifact_id``, starts with the story source and a
plan index attached, and flips the story into agent mode so loop runs are
refused on it from then on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from src.infrastructure.database.migrations import initialize_database
from src.infrastructure.database.tables import schema_version_table
from src.settings import Settings
from tests.integration.test_work_planning_routes import (
    _create_work,
    _entry_agent_brief,
    _start_plan,
)


def _post_story_agent(
    client: TestClient, folder: Path, artifact_id: str, contexts: list[dict] | None = None
):
    folder.mkdir(parents=True, exist_ok=True)
    return client.post(
        "/api/works/WRK-001/agents",
        json={
            "name": "Story Dev",
            "persona": "developer",
            "role": "Implement the story",
            "provider": "amp",
            "model": "smart",
            "folder": str(folder),
            "artifact_id": artifact_id,
            "contexts": contexts or [],
        },
    )


def _approved_plan(client: TestClient, settings: Settings) -> Path:
    _create_work(client)
    root = settings.workspace_root / "repo"
    _start_plan(client, root)
    assert client.post("/api/works/WRK-001/plan/approve").status_code == 200
    return root


def test_story_agent_persists_artifact_id(app_client: TestClient, test_settings: Settings) -> None:
    root = _approved_plan(app_client, test_settings)

    res = _post_story_agent(app_client, root, "story-001")

    assert res.status_code == 201, res.text
    body = res.json()
    assert body["artifact_id"] == "story-001"
    agent_json = json.loads(
        (
            test_settings.workspace_root
            / "works"
            / "WRK-001"
            / "agents"
            / body["slug"]
            / "agent.json"
        ).read_text()
    )
    assert agent_json["artifact_id"] == "story-001"
    listed = app_client.get("/api/works/WRK-001/agents").json()
    assert [a["artifact_id"] for a in listed] == ["story-001"]


def test_canvas_agent_json_omits_artifact_id(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    folder = test_settings.workspace_root / "plain"
    folder.mkdir(parents=True)

    res = app_client.post(
        "/api/works/WRK-001/agents",
        json={
            "name": "Canvas",
            "persona": "developer",
            "role": "dev",
            "provider": "amp",
            "model": "smart",
            "folder": str(folder),
        },
    )

    assert res.status_code == 201, res.text
    assert res.json()["artifact_id"] is None
    agent_json = json.loads(
        (
            test_settings.workspace_root
            / "works"
            / "WRK-001"
            / "agents"
            / res.json()["slug"]
            / "agent.json"
        ).read_text()
    )
    assert "artifact_id" not in agent_json


def test_story_agent_gets_story_and_plan_index_as_context(
    app_client: TestClient, test_settings: Settings
) -> None:
    root = _approved_plan(app_client, test_settings)
    detail = app_client.get("/api/works/WRK-001/plan/artifacts/story-001").json()
    story_ref = detail["artifact"]["source_ref"]

    res = _post_story_agent(
        app_client, root, "story-001", contexts=[{"type": "file", "value": story_ref}]
    )

    assert res.status_code == 201, res.text
    agent_json = json.loads(
        (
            test_settings.workspace_root
            / "works"
            / "WRK-001"
            / "agents"
            / res.json()["slug"]
            / "agent.json"
        ).read_text()
    )
    contexts = agent_json["contexts"]
    assert contexts[0] == {"type": "file", "value": story_ref}
    assert contexts[1]["type"] == "text"
    index = contexts[1]["value"]
    assert index.startswith("# Plan index")
    assert story_ref in index
    assert "intent.md" in index and "design-guide.md" in index
    # The client's duplicate story chip is folded into the locked one.
    assert sum(1 for c in contexts if c.get("value") == story_ref) == 1


def test_story_agent_switches_story_into_agent_mode(
    app_client: TestClient, test_settings: Settings
) -> None:
    root = _approved_plan(app_client, test_settings)
    before = app_client.get("/api/works/WRK-001/plan").json()
    assert _story(before)["work_mode"] is None

    assert _post_story_agent(app_client, root, "story-001").status_code == 201

    after = app_client.get("/api/works/WRK-001/plan").json()
    assert _story(after)["work_mode"] == "agents"


def test_loop_start_refused_on_agent_mode_story(
    app_client: TestClient, test_settings: Settings
) -> None:
    root = _approved_plan(app_client, test_settings)
    assert _post_story_agent(app_client, root, "story-001").status_code == 201

    res = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"brief": _entry_agent_brief()},
    )

    assert res.status_code == 409, res.text


@pytest.mark.parametrize(
    ("artifact_id", "expected"),
    [("story-999", 404), ("brief", 422)],
)
def test_story_agent_rejects_bad_artifact(
    app_client: TestClient, test_settings: Settings, artifact_id: str, expected: int
) -> None:
    root = _approved_plan(app_client, test_settings)

    res = _post_story_agent(app_client, root, artifact_id)

    assert res.status_code == expected, res.text
    assert app_client.get("/api/works/WRK-001/agents").json() == []


def test_story_agent_404_when_planning_not_started(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)

    res = _post_story_agent(app_client, test_settings.workspace_root / "repo", "story-001")

    assert res.status_code == 404, res.text


def test_v26_upgrade_adds_nullable_agent_artifact_id(isolated_engine: Engine) -> None:
    with isolated_engine.begin() as conn:
        conn.execute(text("ALTER TABLE agents DROP COLUMN artifact_id"))
        conn.execute(schema_version_table.update().values(version=25))

    initialize_database(isolated_engine)

    columns = {column["name"] for column in inspect(isolated_engine).get_columns("agents")}
    assert "artifact_id" in columns


def _story(plan: dict) -> dict:
    return next(a for a in plan["artifacts"] if a["id"] == "story-001")


def test_agent_mode_story_can_be_marked_done(
    app_client: TestClient, test_settings: Settings
) -> None:
    root = _approved_plan(app_client, test_settings)
    assert _post_story_agent(app_client, root, "story-001").status_code == 201

    res = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/accept",
        json={"summary": "Shipped behind the flag.", "changes": "fallback.ts"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["artifact"]["status"] == "accepted"
    summary_path = Path(body["artifact"]["accepted_summary_path"])
    assert summary_path.name == "story-001-agents.md"
    assert "Shipped behind the flag." in summary_path.read_text()
    plan = app_client.get("/api/works/WRK-001/plan").json()
    assert _story(plan)["status"] == "accepted"


def test_marking_done_requires_agent_mode(app_client: TestClient, test_settings: Settings) -> None:
    _approved_plan(app_client, test_settings)

    res = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/accept", json={"summary": "x"}
    )

    assert res.status_code == 409, res.text
