"""Integration tests for source-backed Work planning routes."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.application.http.routes.works import get_chat_supervisor
from src.domain.chatstore.dtos import ChatGrounding, CreateChatRequest
from src.domain.commands.planning import (
    materialize,
    submit_materialization,
)
from src.domain.loop import monitor as loop_monitor
from src.domain.loop import runtime as loop_runtime
from src.domain.loop.builtins import builtin_loop_definition
from src.domain.planning import materialization as planning_materialization
from src.domain.planning.dtos import (
    PlanArtifactEntry,
    PlanningFramework,
    PlanningProfile,
)
from src.domain.planning.frameworks import default_plan_artifacts_dir
from src.domain.planning.models import PlanningSession
from src.main import create_app
from src.settings import Settings


def _create_work(client: TestClient) -> None:
    res = client.post(
        "/api/works",
        json={"name": "Planner", "description": "Refactor the planning loop."},
    )
    assert res.status_code == 201, res.text


def _start_plan(client: TestClient, root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    _mark_framework_ready(root, "bmad")
    artifacts = _write_default_plan_sources(root)
    res = _submit_plan_metadata(client, root, "bmad", "refactor", artifacts)
    assert res.status_code == 200, res.text
    _create_planning_session(client, root, provider="amp", model="smart")
    return res.json()


def _complete_report(summary: str = "Implemented the artifact.") -> dict[str, object]:
    return {
        "outcome": "pass",
        "summary": summary,
        "findings": [],
        "divergences": "None.",
        "skipped_scope": "None.",
        "blocker": "None.",
        "changes": "Updated the assigned artifact implementation.",
        "validation_evidence": "pytest passed",
        "artifact_refs": [],
    }


def _loop_report_text(fields: dict[str, object]) -> str:
    return json.dumps({"atelier_loop_step_report": fields})


def _publish_agent_event(client: TestClient, agent_slug: str, payload: dict[str, object]) -> None:
    portal = client.portal
    assert portal is not None
    published = portal.call(
        client.app.state.supervisor.publish_external_event,
        agent_slug,
        payload,
    )
    if not published:
        client.app.state.workstore.append_transcript_event_with_seq("WRK-001", agent_slug, payload)


def _wait_for_scripted_agent(client: TestClient, agent_slug: str) -> None:
    if not client.app.state.supervisor.is_registered(agent_slug):
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        events = client.app.state.workstore.read_transcript_from_cursor("WRK-001", agent_slug, 0)
        if any(
            event.get("type") == "status_change" and event.get("status") == "idle"
            for event in events
        ):
            return
        time.sleep(0.02)
    raise AssertionError(f"scripted agent did not become idle: {agent_slug}")


def _append_agent_loop_report(
    client: TestClient, agent_slug: str, fields: dict[str, object]
) -> None:
    _wait_for_scripted_agent(client, agent_slug)
    _publish_agent_event(
        client,
        agent_slug,
        {
            "type": "message_complete",
            "ts": datetime.now(UTC).isoformat(),
            "text": _loop_report_text(fields),
        },
    )
    _publish_agent_event(
        client,
        agent_slug,
        {"type": "status_change", "status": "idle"},
    )


def _append_stage_report(
    client: TestClient,
    agent_slug: str,
    *,
    outcome: str,
    summary: str,
    findings: list[str] | None = None,
    validation_evidence: str = "",
) -> None:
    _wait_for_scripted_agent(client, agent_slug)
    _publish_agent_event(
        client,
        agent_slug,
        {
            "type": "message_complete",
            "ts": datetime.now(UTC).isoformat(),
            "text": json.dumps(
                {
                    "atelier_loop_step_report": {
                        "outcome": outcome,
                        "summary": summary,
                        "findings": findings or [],
                        "changes": "Implemented the assigned scope.",
                        "validation_evidence": validation_evidence,
                        "divergences": "None.",
                        "skipped_scope": "None.",
                        "blocker": "None.",
                        "artifact_refs": [],
                    }
                }
            ),
        },
    )
    _publish_agent_event(
        client,
        agent_slug,
        {"type": "status_change", "status": "idle"},
    )


def _wait_run(
    client: TestClient,
    artifact_id: str,
    run_id: str,
    status: str,
    *,
    loop_status: str | None = None,
    timeout: float = 4.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        res = client.get(f"/api/works/WRK-001/plan/artifacts/{artifact_id}/runs/{run_id}")
        assert res.status_code == 200, res.text
        last = res.json()
        if last["status"] == status and (loop_status is None or last["loop_status"] == loop_status):
            return last
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {status}/{loop_status}: {last}")


def _wait_stage(
    client: TestClient,
    run_id: str,
    stage_id: str,
    *,
    status: str = "running",
    timeout: float = 4.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["loop_current_stage_id"] == stage_id:
            stage = next(item for item in last["loop_stages"] if item["id"] == stage_id)
            if stage["status"] == status:
                return last
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {stage_id}/{status}: {last}")


def _entry_agent_brief(
    stage_id: str = "implementation",
    *,
    provider: str = "amp",
    model: str = "smart",
    notes: dict[str, str] | None = None,
) -> dict:
    """A brief pinning the entry stage's provider, plus any required notes.

    A story run has nothing to inherit a provider from, so the entry stage
    must name one -- either in the loop definition or here. ``notes`` fills
    stages the definition marks ``note_required``; run setup will not let a
    user start without them, so a brief that skips them is rejected.
    """
    return {
        "goal": "Story run",
        "stages": [
            {"stage_id": stage_id, "agent": {"provider": provider, "model": model}},
            *(
                {"stage_id": key, "note": value}
                for key, value in (notes or {}).items()
            ),
        ],
    }


#: Builtin review stages declare ``note_required``. Run setup blocks Start
#: until they are filled, so a realistic brief carries them -- and only the
#: ones its loop actually has, since an unknown stage id is itself rejected.
_REVIEWED_NOTES = {"code-review": "Check the migration boundary."}
_SECURE_NOTES = {**_REVIEWED_NOTES, "security-review": "Check for secrets."}


def _start_artifact_run(
    client: TestClient,
    settings: Settings,
    artifact_id: str = "story-001",
) -> tuple[dict[str, object], str]:
    res = client.post(
        f"/api/works/WRK-001/plan/artifacts/{artifact_id}/runs",
        json={"brief": _entry_agent_brief()},
    )
    assert res.status_code == 200, res.text
    run = res.json()["artifact"]["runs"][0]
    run_id = str(run["id"])
    return _active_stage_agent(client, run_id, artifact_id=artifact_id), run_id


def _active_stage_agent(
    client: TestClient,
    run_id: str,
    *,
    artifact_id: str = "story-001",
) -> dict[str, object]:
    """Return the fresh agent currently executing a Planning loop stage."""
    run = client.get(f"/api/works/WRK-001/plan/artifacts/{artifact_id}/runs/{run_id}").json()
    stage = next(item for item in run["loop_stages"] if item["id"] == run["loop_current_stage_id"])
    agent_slug = stage["agent_slug"]
    assert isinstance(agent_slug, str)
    return next(
        item
        for item in client.get("/api/works/WRK-001/agents").json()
        if item["slug"] == agent_slug
    )


def _complete_artifact_run(
    client: TestClient,
    agent_slug: str,
    run_id: str,
    *,
    artifact_id: str = "story-001",
    summary: str = "Implemented the artifact.",
) -> dict[str, object]:
    _append_agent_loop_report(client, agent_slug, _complete_report(summary))
    return _wait_run(
        client,
        artifact_id,
        run_id,
        "completed_pending_review",
        loop_status="awaiting_approval",
    )


def _write_default_plan_sources(root: Path) -> list[dict[str, object]]:
    artifact_dir = _artifact_root(root)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "intent.md").write_text(
        "# Intent\n\n## Objective\n\nMake work planning source-backed.\n"
    )
    (artifact_dir / "design-guide.md").write_text(
        "# Design Guide\n\n## Validation\n\n- Run tests.\n"
    )
    stories = artifact_dir / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    (stories / "story-001.md").write_text(
        """# Story 001: Plan Planner

## Scope

Build source-backed planning.

## Acceptance Criteria

- Source-backed planning works.

## Dependencies

- None
"""
    )
    return [
        {
            "path": "intent.md",
            "title": "Intent",
            "artifact_kind": "brief",
            "executable": False,
            "dependencies": [],
        },
        {
            "path": "design-guide.md",
            "title": "Design Guide",
            "artifact_kind": "architecture",
            "executable": False,
            "dependencies": [],
        },
        {
            "path": "stories/story-001.md",
            "title": "Story 001: Plan Planner",
            "artifact_kind": "story",
            "executable": True,
            "dependencies": [],
        },
    ]


def _submit_plan_metadata(
    client: TestClient,
    root: Path,
    framework: PlanningFramework,
    profile: PlanningProfile,
    artifacts: list[dict[str, object]],
    plan_artifacts_dir: str | None = None,
) -> Any:
    submit_materialization.execute(
        client.app.state.workstore,
        client.app.state.planningfiles,
        client.app.state.loop_runs,
        submit_materialization.SubmitPlanMaterializationRequest(
            work_slug="WRK-001",
            root_path=str(root),
            plan_artifacts_dir=plan_artifacts_dir,
            framework=framework,
            profile=profile,
            artifacts=tuple(_artifact_entry(item) for item in artifacts),
        ),
    )
    return client.get("/api/works/WRK-001/plan")


def _artifact_entry(item: dict[str, object]) -> PlanArtifactEntry:
    return PlanArtifactEntry(
        path=str(item["path"]),
        title=str(item["title"]),
        artifact_kind=item["artifact_kind"],  # type: ignore[arg-type]
        executable=bool(item["executable"]),
        dependencies=tuple(str(dep) for dep in item.get("dependencies", [])),
    )


def _mark_framework_ready(root: Path, framework: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    marker = {
        "bmad": ".bmad-core",
        "spec": ".specify",
        "openspec": ".openspec",
    }.get(framework)
    if marker:
        (root / marker).mkdir(parents=True, exist_ok=True)


def test_start_plan_indexes_precreated_bmad_sources(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repos" / "planner"
    body = _start_plan(app_client, repo_root)

    planning_dir = test_settings.workspace_root / "works" / "WRK-001" / "planning"
    artifact_dir = _artifact_root(repo_root)
    assert body["work_slug"] == "WRK-001"
    assert body["root_path"] == str(repo_root)
    assert body["planning_path"] == str(planning_dir)
    assert body["plan_artifacts_path"] == str(artifact_dir)
    assert body["framework"] == "bmad"
    assert body["profile"] == "refactor"
    assert body["phase"] == "planned"
    assert body["overview"]["total"] == 3
    assert {a["id"] for a in body["artifacts"]} == {
        "brief",
        "architecture",
        "story-001",
    }
    assert (planning_dir / "manifest.json").exists()
    assert not (planning_dir / "intent.md").exists()
    assert (artifact_dir / "intent.md").exists()
    assert (artifact_dir / "design-guide.md").exists()
    assert not (artifact_dir / "design-guidance.md").exists()
    assert (artifact_dir / "stories" / "story-001.md").exists()
    manifest = json.loads((planning_dir / "manifest.json").read_text())
    assert [
        (item["path"], item["artifact_kind"], item["executable"]) for item in manifest["artifacts"]
    ] == [
        ("intent.md", "brief", False),
        ("design-guide.md", "architecture", False),
        ("stories/story-001.md", "story", True),
    ]
    assert all("content" not in item for item in manifest["artifacts"])


def test_start_plan_accepts_a_custom_plan_artifacts_dir(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repos" / "planner"
    _mark_framework_ready(repo_root, "bmad")
    artifact_dir = repo_root / "bmad" / "WRK-001"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "intent.md").write_text("# Intent\n")
    (artifact_dir / "stories").mkdir()
    (artifact_dir / "stories" / "story-001.md").write_text(
        "# Story 001\n\n## Acceptance Criteria\n\n- Uses custom root.\n"
    )

    res = _submit_plan_metadata(
        app_client,
        repo_root,
        "bmad",
        "feature",
        [
            {
                "path": "intent.md",
                "title": "Intent",
                "artifact_kind": "brief",
                "executable": False,
                "dependencies": [],
            },
            {
                "path": "stories/story-001.md",
                "title": "Story 001",
                "artifact_kind": "story",
                "executable": True,
                "dependencies": [],
            },
        ],
        plan_artifacts_dir="bmad/WRK-001",
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["plan_artifacts_path"] == str(artifact_dir)
    manifest = json.loads(
        (
            test_settings.workspace_root
            / "works" / "WRK-001" / "planning" / "manifest.json"
        ).read_text()
    )
    assert manifest["plan_artifacts_dir"] == "bmad/WRK-001"
    assert {item["path"] for item in manifest["artifacts"]} == {
        "intent.md",
        "stories/story-001.md",
    }


def test_start_plan_records_framework_and_profile_depth(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repos" / "planner"
    _mark_framework_ready(repo_root, "spec")
    artifact_dir = repo_root / "plans" / "spec-import"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "spec.md").write_text("# Outage Spec\n")
    (artifact_dir / "scenarios.md").write_text("# Outage Scenarios\n")
    (artifact_dir / "acceptance.md").write_text("# Outage Acceptance\n")
    (artifact_dir / "tasks").mkdir()
    (artifact_dir / "tasks" / "task-001.md").write_text(
        "# Patch Task\n\n## Acceptance Criteria\n\n- Outage is patched.\n"
    )

    res = _submit_plan_metadata(
        app_client,
        repo_root,
        "spec",
        "hotfix",
        [
            {
                "path": "spec.md",
                "title": "Outage Spec",
                "artifact_kind": "spec",
                "executable": False,
                "dependencies": [],
            },
            {
                "path": "scenarios.md",
                "title": "Outage Scenarios",
                "artifact_kind": "scenario",
                "executable": False,
                "dependencies": [],
            },
            {
                "path": "acceptance.md",
                "title": "Outage Acceptance",
                "artifact_kind": "acceptance",
                "executable": False,
                "dependencies": [],
            },
            {
                "path": "tasks/task-001.md",
                "title": "Patch Task",
                "artifact_kind": "task",
                "executable": True,
                "dependencies": ["spec.md"],
            },
        ],
        plan_artifacts_dir="plans/spec-import",
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["framework"] == "spec"
    assert body["profile"] == "hotfix"
    assert body["depth"] == "minimal"
    assert {a["id"] for a in body["artifacts"]} == {
        "spec",
        "scenarios",
        "acceptance",
        "task-001",
    }


def test_plan_manifest_dependencies_are_path_based_metadata(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repos" / "planner"
    _start_plan(app_client, repo_root)
    manifest_path = (
        test_settings.workspace_root / "works" / "WRK-001" / "planning" / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text())
    for item in manifest["artifacts"]:
        if item["path"] == "stories/story-001.md":
            item["dependencies"] = ["intent.md"]
    manifest_path.write_text(json.dumps(manifest))

    refreshed = app_client.get("/api/works/WRK-001/plan")

    assert refreshed.status_code == 200, refreshed.text
    story = next(a for a in refreshed.json()["artifacts"] if a["id"] == "story-001")
    assert story["dependencies"] == ["brief"]


def test_start_plan_indexes_precreated_files_without_content_payload(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "spec")
    planning_dir = test_settings.workspace_root / "works" / "WRK-001" / "planning"
    artifact_dir = _artifact_root(repo_root, "spec")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "spec.md").write_text("# Import Spec\n\n## Behavior\n\nImport CSV.\n")
    (artifact_dir / "tasks").mkdir()
    (artifact_dir / "tasks" / "task-001.md").write_text(
        "# Import Task\n\n## Scope\n\nBuild it.\n\n## Acceptance Criteria\n\n- Imports work.\n"
    )

    res = _submit_plan_metadata(
        app_client,
        repo_root,
        "spec",
        "feature",
        [
            {
                "path": "spec.md",
                "title": "Import Spec",
                "artifact_kind": "spec",
                "executable": False,
                "dependencies": [],
            },
            {
                "path": "tasks/task-001.md",
                "title": "Import Task",
                "artifact_kind": "task",
                "executable": True,
                "dependencies": ["spec.md"],
            },
        ],
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["framework"] == "spec"
    assert body["phase"] == "planned"
    assert {a["id"] for a in body["artifacts"]} == {"spec", "task-001"}
    task = next(a for a in body["artifacts"] if a["id"] == "task-001")
    assert task["kind"] == "task"
    assert task["dependencies"] == ["spec"]
    manifest = json.loads((planning_dir / "manifest.json").read_text())
    assert all("content" not in item for item in manifest["artifacts"])


def test_start_plan_rejects_missing_files(app_client: TestClient, test_settings: Settings) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")

    with pytest.raises(
        submit_materialization.InvalidPlanMaterialization,
        match="artifact file is missing",
    ):
        submit_materialization.execute(
            app_client.app.state.workstore,
            app_client.app.state.planningfiles,
            app_client.app.state.loop_runs,
            submit_materialization.SubmitPlanMaterializationRequest(
                work_slug="WRK-001",
                root_path=str(repo_root),
                framework="bmad",
                profile="feature",
                artifacts=(
                    PlanArtifactEntry(
                        path="stories/story-001.md",
                        title="Missing Story",
                        artifact_kind="story",
                        executable=True,
                    ),
                ),
            ),
        )


def test_start_plan_requires_selected_framework_setup(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repos" / "planner"
    repo_root.mkdir(parents=True, exist_ok=True)

    with pytest.raises(
        submit_materialization.PlanningFrameworkNotReady,
        match="BMAD is not initialized",
    ):
        submit_materialization.execute(
            app_client.app.state.workstore,
            app_client.app.state.planningfiles,
            app_client.app.state.loop_runs,
            submit_materialization.SubmitPlanMaterializationRequest(
                work_slug="WRK-001",
                root_path=str(repo_root),
                framework="bmad",
                profile="feature",
                artifacts=(
                    PlanArtifactEntry(
                        path="stories/story-001.md",
                        title="Story",
                        artifact_kind="story",
                        executable=True,
                    ),
                ),
            ),
        )


def test_planning_framework_status_reports_setup_command(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    missing = app_client.post(
        "/api/works/WRK-001/plan/framework-status",
        json={"root_path": str(repo_root), "framework": "openspec"},
    )

    assert missing.status_code == 200, missing.text
    assert missing.json()["ready"] is False
    assert missing.json()["setup_command"] == ["openspec", "init"]

    _mark_framework_ready(repo_root, "openspec")
    ready = app_client.post(
        "/api/works/WRK-001/plan/framework-status",
        json={"root_path": str(repo_root), "framework": "openspec"},
    )
    assert ready.json()["ready"] is True


def test_planning_framework_status_accepts_legacy_bmad_marker(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    (repo_root / "_bmad").mkdir(parents=True, exist_ok=True)

    ready = app_client.post(
        "/api/works/WRK-001/plan/framework-status",
        json={"root_path": str(repo_root), "framework": "bmad"},
    )

    assert ready.status_code == 200, ready.text
    assert ready.json()["ready"] is True
    assert ready.json()["markers"] == ["_bmad"]


def test_planning_framework_status_accepts_bmad_folder(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    (repo_root / "bmad").mkdir(parents=True, exist_ok=True)

    ready = app_client.post(
        "/api/works/WRK-001/plan/framework-status",
        json={"root_path": str(repo_root), "framework": "bmad"},
    )

    assert ready.status_code == 200, ready.text
    assert ready.json()["ready"] is True
    assert ready.json()["markers"] == ["bmad"]


def test_start_planning_chat_uses_backend_prompt_and_framework_gate(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    missing = app_client.post(
        "/api/works/WRK-001/planning-chat",
        json={
            "root_path": str(repo_root),
            "idea": "Plan a CSV import feature.",
            "framework": "bmad",
            "profile": "feature",
            "provider": "codex",
            "model": "gpt-5.4",
            "options": {},
        },
    )

    assert missing.status_code == 409

    _mark_framework_ready(repo_root, "bmad")
    created = app_client.post(
        "/api/works/WRK-001/planning-chat",
        json={
            "root_path": str(repo_root),
            "idea": "Plan a CSV import feature.",
            "plan_artifacts_dir": "bmad/csv-import",
            "framework": "bmad",
            "profile": "feature",
            "provider": "codex",
            "model": "gpt-5.4",
            "options": {"approval_mode": "never"},
        },
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["title"] == "Planning"
    assert body["grounding"] == {"kind": "work", "ref": "WRK-001"}
    assert body["working_directory"] == str(repo_root)
    first = body["transcript"][0]["body"]
    assert "Start planning WRK-001" in first
    assert "with BMAD" in first
    assert "Goal: Plan a CSV import feature." in first
    assert "atelier_planning_ready" not in first
    session = app_client.app.state.planning_sessions.get_by_work_slug("WRK-001")
    assert session.work_slug == "WRK-001"
    assert session.planning_chat_slug == body["slug"]
    assert session.root_path == str(repo_root)
    assert session.plan_artifacts_dir == "bmad/csv-import"
    assert session.provider == "codex"
    assert session.model == "gpt-5.4"
    assert session.options == {"approval_mode": "never"}
    assert body["options"]["planning_config"]["plan_artifacts_dir"] == "bmad/csv-import"


def test_start_planning_setup_chat_creates_visible_installer_chat(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    created = app_client.post(
        "/api/works/WRK-001/planning-setup-chat",
        json={
            "root_path": str(repo_root),
            "framework": "bmad",
            "profile": "feature",
            "provider": "codex",
            "model": "gpt-5.4",
            "options": {"sandbox": "read-only"},
        },
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["title"] == "Planning setup: BMAD"
    assert body["grounding"] == {"kind": "work", "ref": "WRK-001"}
    assert body["working_directory"] == str(repo_root)
    first = body["transcript"][0]["body"]
    assert "Set up the selected Atelier planning framework" in first
    assert "npx bmad-method install" in first
    record = app_client.app.state.chatstore.get_chat(body["slug"])
    assert record.chat.options["sandbox"] == "workspace-write"
    assert record.chat.options["approval_mode"] == "on-request"

    reused = app_client.post(
        "/api/works/WRK-001/planning-setup-chat",
        json={
            "root_path": str(repo_root),
            "framework": "bmad",
            "profile": "feature",
            "provider": "codex",
            "model": "gpt-5.4",
            "options": {},
        },
    )
    assert reused.json()["slug"] == body["slug"]


def test_start_plan_finalizes_existing_materializer_report(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "spec")
    materializer = _create_materializer_chat(app_client, repo_root)
    _create_planning_session(
        app_client,
        repo_root,
        framework="spec",
        profile="feature",
        provider="codex",
        model="gpt-5.4",
        plan_artifacts_dir="plans/spec-import",
    )
    artifact_dir = repo_root / "plans" / "spec-import"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "spec.md").write_text("# Import Spec\n")
    (artifact_dir / "tasks").mkdir()
    (artifact_dir / "tasks" / "task-001.md").write_text(
        "# Import Task\n\n## Acceptance Criteria\n\n- Imports work.\n"
    )
    report = {
        "atelier_plan_materialization": {
            "artifacts": [
                {
                    "path": "spec.md",
                    "title": "Import Spec",
                    "artifact_kind": "spec",
                    "executable": False,
                    "dependencies": [],
                },
                {
                    "path": "tasks/task-001.md",
                    "title": "Import Task",
                    "artifact_kind": "task",
                    "executable": True,
                    "dependencies": ["spec.md"],
                },
            ]
        }
    }
    app_client.app.state.chatstore.append_transcript_event_with_seq(
        materializer,
        {
            "type": "message_complete",
            "ts": "2026-06-29T00:00:00+00:00",
            "text": f"Done.\n{json.dumps(report)}\n",
        },
    )

    finalized = app_client.post(
        "/api/works/WRK-001/plan",
        json={},
    )

    assert finalized.status_code == 200, finalized.text
    body = finalized.json()
    assert body["materialization_status"]["state"] == "complete"
    plan = body["plan"]
    assert plan["framework"] == "spec"
    assert plan["plan_artifacts_path"] == str(artifact_dir)
    assert plan["phase"] == "planned"
    assert {artifact["id"] for artifact in plan["artifacts"]} == {
        "spec",
        "task-001",
    }
    task = next(a for a in plan["artifacts"] if a["id"] == "task-001")
    assert task["dependencies"] == ["spec"]


def test_materializer_config_mismatch_creates_new_chat(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")
    stale_slug = _create_materializer_chat(
        app_client,
        repo_root,
        provider="claude-acp",
        model="default",
        options={"permission_mode": "acceptEdits"},
    )

    record, _status = planning_materialization.start_materialization_chat(
        app_client.app.state.workstore,
        app_client.app.state.chatstore,
        work_slug="WRK-001",
        root_path=str(repo_root),
        framework="bmad",
        profile="feature",
        provider="codex-acp",
        model="gpt-5.5",
        options={"reasoning_effort": "xhigh"},
        planning_chat_slug=None,
    )

    assert record.chat.slug != stale_slug
    assert record.chat.provider == "codex-acp"
    assert record.chat.model == "gpt-5.5"
    assert record.chat.options["reasoning_effort"] == "xhigh"
    assert record.chat.options["mode"] == "agent"


def test_start_plan_requires_planning_session(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")

    finalized = app_client.post(
        "/api/works/WRK-001/plan",
        json={},
    )

    assert finalized.status_code == 404
    assert "planning session not found" in finalized.text


def test_start_plan_rejects_artifact_root_outside_work_folder(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")
    _create_planning_session(
        app_client,
        repo_root,
        framework="bmad",
        profile="feature",
        provider="codex",
        model="gpt-5.4",
        plan_artifacts_dir="../outside",
    )

    finalized = app_client.post(
        "/api/works/WRK-001/plan",
        json={},
    )

    assert finalized.status_code == 422
    assert "invalid framework output folder" in finalized.text


def test_materializer_retries_incomplete_turn(
    app_client: TestClient, test_settings: Settings, monkeypatch: Any
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")
    artifact_dir = _artifact_root(repo_root)
    _create_planning_session(app_client, repo_root)
    chatstore = app_client.app.state.chatstore
    fake_supervisor = _FakeMaterializerSupervisor(chatstore)
    ensure_calls = 0

    async def fake_ensure(*args: Any, **kwargs: Any) -> bool:
        nonlocal ensure_calls
        ensure_calls += 1
        assert "Planning materializer" in kwargs["system_prompt_override"]
        assert "selected framework is BMAD" in kwargs["system_prompt_override"]
        chat_slug = args[-1]
        if ensure_calls == 1:
            chatstore.append_transcript_event_with_seq(
                chat_slug,
                {
                    "type": "status_change",
                    "status": "idle",
                    "ts": "2026-06-29T00:00:00+00:00",
                },
            )
        else:
            _write_materializer_report(chatstore, chat_slug, artifact_dir)
        return True

    monkeypatch.setattr(materialize.chat_runtime, "ensure_chat_runtime", fake_ensure)

    view = asyncio.run(
        materialize.execute(
            app_client.app.state.workstore,
            chatstore,
            app_client.app.state.projectstore,
            app_client.app.state.planningfiles,
            app_client.app.state.loop_runs,
            app_client.app.state.planning_sessions,
            fake_supervisor,
            test_settings,
            materialize.MaterializePlanRequest(
                work_slug="WRK-001",
                root_path=str(repo_root),
                framework="bmad",
                profile="feature",
                provider="codex",
                model="gpt-5.4",
                options={},
            ),
        )
    )

    assert view.phase == "planned"
    assert {artifact.id for artifact in view.artifacts} == {"story-001"}
    assert fake_supervisor.permissions == []
    assert len(fake_supervisor.inputs) == 1
    assert "atelier_plan_materialization" in fake_supervisor.inputs[0]
    assert "Do not restart planning, scan the repository" in fake_supervisor.inputs[0]


def test_failed_materializer_retry_starts_fresh_provider_session(
    app_client: TestClient, test_settings: Settings, monkeypatch: Any
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")
    artifact_dir = _artifact_root(repo_root)
    _create_planning_session(app_client, repo_root)
    chat_slug = _create_materializer_chat(app_client, repo_root)
    chatstore = app_client.app.state.chatstore
    chatstore.set_chat_session_id(chat_slug, "dead-provider-session")
    chatstore.append_transcript_event_with_seq(
        chat_slug,
        {
            "type": "error",
            "ts": "2026-06-29T00:00:00+00:00",
            "message": "Connection closed",
        },
    )
    fake_supervisor = _FakeMaterializerSupervisor(chatstore)

    async def fake_ensure(*args: Any, **_kwargs: Any) -> bool:
        requested_slug = args[-1]
        assert chatstore.get_chat(requested_slug).chat.session_id is None
        _write_materializer_report(chatstore, requested_slug, artifact_dir)
        return True

    monkeypatch.setattr(materialize.chat_runtime, "ensure_chat_runtime", fake_ensure)

    view = asyncio.run(
        materialize.execute(
            app_client.app.state.workstore,
            chatstore,
            app_client.app.state.projectstore,
            app_client.app.state.planningfiles,
            app_client.app.state.loop_runs,
            app_client.app.state.planning_sessions,
            fake_supervisor,
            test_settings,
            materialize.MaterializePlanRequest(
                work_slug="WRK-001",
                fresh_session=True,
            ),
        )
    )

    assert view.phase == "planned"
    assert fake_supervisor.stopped == [chat_slug]
    assert len(fake_supervisor.inputs) == 1
    assert "authoritative" in fake_supervisor.inputs[0]
    assert "original_materialization_brief" in fake_supervisor.inputs[0]


def test_quiet_materializer_does_not_receive_automatic_input(
    app_client: TestClient, test_settings: Settings, monkeypatch: Any
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")
    _create_planning_session(app_client, repo_root)
    fake_supervisor = _FakeMaterializerSupervisor(app_client.app.state.chatstore)

    async def fake_ensure(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(materialize.chat_runtime, "ensure_chat_runtime", fake_ensure)
    monkeypatch.setattr(materialize, "_POLL_INTERVAL_SECONDS", 0.001)

    async def scenario() -> None:
        task = asyncio.create_task(
            materialize.execute(
                app_client.app.state.workstore,
                app_client.app.state.chatstore,
                app_client.app.state.projectstore,
                app_client.app.state.planningfiles,
                app_client.app.state.loop_runs,
                app_client.app.state.planning_sessions,
                fake_supervisor,
                test_settings,
                materialize.MaterializePlanRequest(work_slug="WRK-001"),
            )
        )
        await asyncio.sleep(0.01)
        assert fake_supervisor.inputs == []
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_connection_closed_materializer_recovers_once(
    app_client: TestClient, test_settings: Settings, monkeypatch: Any
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")
    artifact_dir = _artifact_root(repo_root)
    _create_planning_session(app_client, repo_root)
    chat_slug = _create_materializer_chat(app_client, repo_root)
    chatstore = app_client.app.state.chatstore
    chatstore.set_chat_session_id(chat_slug, "closed-provider-session")
    fake_supervisor = _FakeMaterializerSupervisor(chatstore)
    ensure_calls = 0

    async def fake_ensure(*args: Any, **_kwargs: Any) -> bool:
        nonlocal ensure_calls
        ensure_calls += 1
        requested_slug = args[-1]
        if ensure_calls == 1:
            chatstore.append_transcript_event_with_seq(
                requested_slug,
                {
                    "type": "error",
                    "ts": "2026-06-29T00:00:01+00:00",
                    "message": "Connection closed",
                },
            )
            chatstore.append_transcript_event_with_seq(
                requested_slug,
                {
                    "type": "status_change",
                    "ts": "2026-06-29T00:00:01+00:00",
                    "status": "idle",
                },
            )
        else:
            assert chatstore.get_chat(requested_slug).chat.session_id is None
            _write_materializer_report(chatstore, requested_slug, artifact_dir)
        return True

    monkeypatch.setattr(materialize.chat_runtime, "ensure_chat_runtime", fake_ensure)

    view = asyncio.run(
        materialize.execute(
            app_client.app.state.workstore,
            chatstore,
            app_client.app.state.projectstore,
            app_client.app.state.planningfiles,
            app_client.app.state.loop_runs,
            app_client.app.state.planning_sessions,
            fake_supervisor,
            test_settings,
            materialize.MaterializePlanRequest(work_slug="WRK-001"),
        )
    )

    assert view.phase == "planned"
    assert fake_supervisor.stopped == [chat_slug]
    assert ensure_calls == 2
    assert len(fake_supervisor.inputs) == 1
    assert "fresh provider session" in fake_supervisor.inputs[0]


def test_materializer_resume_does_not_auto_answer_pending_permission(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")
    chat_slug = _create_materializer_chat(app_client, repo_root)
    fake_supervisor = _FakeMaterializerSupervisor()
    app_client.app.state.chatstore.append_transcript_event_with_seq(
        chat_slug,
        {
            "type": "permission_request",
            "ts": "2026-06-29T00:00:00+00:00",
            "request_id": "safe",
            "tool_id": "tool-safe",
            "tool_name": "Bash",
            "tool_input": {"command": "find _bmad-output/WRK-001 -type f"},
        },
    )

    result = asyncio.run(
        materialize.try_finalize_existing(
            app_client.app.state.workstore,
            app_client.app.state.chatstore,
            app_client.app.state.planningfiles,
            app_client.app.state.loop_runs,
            fake_supervisor,
            materialize.MaterializePlanRequest(
                work_slug="WRK-001",
                root_path=str(repo_root),
                framework="bmad",
                profile="feature",
                provider="claude-acp",
                model="default",
                options={},
            ),
            chat_slug,
        )
    )

    assert result is None
    assert fake_supervisor.permissions == []


def test_finish_plan_moves_conversation_to_planned_phase(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")

    res = app_client.post("/api/works/WRK-001/plan/finish")

    assert res.status_code == 200, res.text
    assert res.json()["phase"] == "planned"


def test_legacy_design_guidance_file_is_still_indexed(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    artifact_dir = _artifact_root(test_settings.workspace_root / "repo")
    (artifact_dir / "design-guide.md").unlink()
    (artifact_dir / "design-guidance.md").write_text("# Legacy Design Guidance\n")

    res = app_client.get("/api/works/WRK-001/plan")

    assert res.status_code == 200, res.text
    architecture = next(a for a in res.json()["artifacts"] if a["id"] == "architecture")
    assert architecture["path"] == "design-guidance.md"


def test_get_plan_returns_404_before_planning_starts(app_client: TestClient) -> None:
    _create_work(app_client)

    res = app_client.get("/api/works/WRK-001/plan")

    assert res.status_code == 404


def test_update_artifact_persists_markdown(app_client: TestClient, test_settings: Settings) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    detail = app_client.get("/api/works/WRK-001/plan/artifacts/story-001").json()

    res = app_client.put(
        "/api/works/WRK-001/plan/artifacts/story-001",
        json={
            "expected_hash": detail["artifact"]["source_hash"],
            "content": detail["content"] + "\n## Implementation Notes\n\nKeep it small.\n",
        },
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["artifact"]["source_hash"] != detail["artifact"]["source_hash"]
    assert "Keep it small." in body["content"]


def test_update_artifact_updates_existing_approval_baseline(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    approve = app_client.post("/api/works/WRK-001/plan/approve")
    assert approve.status_code == 200, approve.text
    detail = app_client.get("/api/works/WRK-001/plan/artifacts/story-001").json()

    res = app_client.put(
        "/api/works/WRK-001/plan/artifacts/story-001",
        json={
            "expected_hash": detail["artifact"]["source_hash"],
            "content": detail["content"] + "\n## Implementation Notes\n\nApproved on save.\n",
        },
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["artifact"]["status"] == "approved"
    assert "Approve the latest source changes" not in " ".join(body["artifact"]["launch_blockers"])
    view = app_client.get("/api/works/WRK-001/plan").json()
    story = next(a for a in view["artifacts"] if a["id"] == "story-001")
    assert story["status"] == "approved"
    assert view["stale"] is False


def test_update_artifact_rejects_stale_hash(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    detail = app_client.get("/api/works/WRK-001/plan/artifacts/story-001").json()
    story = _planning_file(test_settings, "stories/story-001.md")
    story.write_text(story.read_text() + "\nManual edit.\n")

    res = app_client.put(
        "/api/works/WRK-001/plan/artifacts/story-001",
        json={
            "expected_hash": detail["artifact"]["source_hash"],
            "content": detail["content"] + "\nConflicting edit.\n",
        },
    )

    assert res.status_code == 409


def test_approve_plan_marks_later_source_change_stale(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    approve = app_client.post("/api/works/WRK-001/plan/approve")
    assert approve.status_code == 200, approve.text
    assert approve.json()["stale"] is False

    story = _planning_file(test_settings, "stories/story-001.md")
    story.write_text(story.read_text() + "\nPost-approval change.\n")
    view = app_client.get("/api/works/WRK-001/plan").json()

    assert view["stale"] is True
    changed = next(a for a in view["artifacts"] if a["id"] == "story-001")
    assert changed["status"] == "changed"


def test_goal_section_counts_as_executable_artifact_readiness(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _mark_framework_ready(repo_root, "bmad")
    artifact_dir = _artifact_root(repo_root)
    stories = artifact_dir / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "intent.md").write_text("# Intent\n")
    (artifact_dir / "design-guide.md").write_text("# Design Guide\n")
    (stories / "spike-001.md").write_text(
        """# Spike 001

## Goal

Choose the right integration path.

## Acceptance Criteria

- The decision is documented.
"""
    )
    res = _submit_plan_metadata(
        app_client,
        repo_root,
        "bmad",
        "feature",
        [
            {
                "path": "intent.md",
                "title": "Intent",
                "artifact_kind": "brief",
                "executable": False,
                "dependencies": [],
            },
            {
                "path": "design-guide.md",
                "title": "Design Guide",
                "artifact_kind": "architecture",
                "executable": False,
                "dependencies": [],
            },
            {
                "path": "stories/spike-001.md",
                "title": "Spike 001",
                "artifact_kind": "spike",
                "executable": True,
                "dependencies": [],
            },
        ],
    )

    assert res.status_code == 200, res.text
    spike = next(a for a in res.json()["artifacts"] if a["id"] == "spike-001")
    assert spike["readiness"] == "ready"


def test_bug_semantic_sections_count_as_artifact_readiness(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _mark_framework_ready(repo_root, "bmad")
    artifact_dir = _artifact_root(repo_root)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "bug-001.md").write_text(
        """# Fix verified failure

## Evidence

The current implementation fails for concurrent callers.

## Required Fix

Isolate caller state and add focused regression coverage.

## Acceptance Criteria

- Concurrent callers do not leak state.
"""
    )
    response = _submit_plan_metadata(
        app_client,
        repo_root,
        "bmad",
        "bugfix",
        [
            {
                "path": "bug-001.md",
                "title": "Fix verified failure",
                "artifact_kind": "bug",
                "executable": True,
                "dependencies": [],
            }
        ],
    )

    bug = response.json()["artifacts"][0]
    assert bug["readiness"] == "ready"


def test_approved_source_doc_dependency_unblocks_launch(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _mark_framework_ready(repo_root, "bmad")
    artifacts = _write_default_plan_sources(repo_root)
    artifacts[-1]["dependencies"] = ["design-guide.md"]
    res = _submit_plan_metadata(app_client, repo_root, "bmad", "feature", artifacts)
    assert res.status_code == 200, res.text

    draft = app_client.get("/api/works/WRK-001/plan").json()
    story = next(a for a in draft["artifacts"] if a["id"] == "story-001")
    assert story["launchable"] is False
    assert (
        "Dependency architecture is not complete or ready for review." in story["launch_blockers"]
    )

    approve = app_client.post("/api/works/WRK-001/plan/approve")
    assert approve.status_code == 200, approve.text
    approved = app_client.get("/api/works/WRK-001/plan").json()
    story = next(a for a in approved["artifacts"] if a["id"] == "story-001")
    assert story["launchable"] is True
    assert story["launch_blockers"] == []


def test_accept_story_writes_summary(app_client: TestClient, test_settings: Settings) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)
    _complete_artifact_run(
        app_client,
        str(agent["slug"]),
        run_id,
        summary="Planner MVP accepted.",
    )
    agent_slug = str(agent["slug"])
    worktree = Path(str(agent["worktree_path"]))
    transcript_before = list(
        app_client.app.state.workstore.read_transcript_from_cursor("WRK-001", agent_slug, 0)
    )
    assert transcript_before
    assert not app_client.app.state.supervisor.is_registered(agent_slug)

    res = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/accept",
        json={"summary": "Planner MVP accepted."},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["artifact"]["status"] == "accepted"
    summary = _planning_file(test_settings, f"summaries/story-001-{run_id}.md")
    assert "Planner MVP accepted." in summary.read_text()
    assert not app_client.app.state.supervisor.is_registered(agent_slug)
    assert agent_slug in {
        str(item["slug"]) for item in app_client.get("/api/works/WRK-001/agents").json()
    }
    assert worktree.exists()
    assert (
        list(app_client.app.state.workstore.read_transcript_from_cursor("WRK-001", agent_slug, 0))[
            : len(transcript_before)
        ]
        == transcript_before
    )


def test_artifact_run_report_and_acceptance_are_recorded(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    agent, run_id = _start_artifact_run(app_client, test_settings)
    run_state = _complete_artifact_run(
        app_client,
        str(agent["slug"]),
        run_id,
        summary="Implemented the source-backed planning path.",
    )
    assert run_state["status"] == "completed_pending_review"
    assert run_state["loop_status"] == "awaiting_approval"
    assert run_state["loop_status_reason"] == (
        "All automatic stages passed; result approval is required."
    )

    accepted = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/accept",
        json={
            "summary": "Accepted after review.",
            "divergences": "None",
            "validation_evidence": "pytest passed",
        },
    )

    assert accepted.status_code == 200, accepted.text
    run_state = accepted.json()["artifact"]["runs"][0]
    assert run_state["status"] == "accepted"
    assert run_state["report_path"].endswith(f"summaries/story-001-{run_id}.md")
    summary = _planning_file(test_settings, f"summaries/story-001-{run_id}.md").read_text()
    assert "## Divergences" in summary
    assert "pytest passed" in summary


def test_default_run_uses_builtin_loop_when_definition_is_omitted(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    payload = app_client.get("/api/loops/atelier-fast").json()
    saved = app_client.post(
        "/api/loops",
        json={
            **payload,
            "name": "Shadowed fast",
            "scope": "work",
            "work_slug": "WRK-001",
            "expected_revision": None,
        },
    )
    assert saved.status_code == 201, saved.text

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"brief": _entry_agent_brief()},
    )

    assert started.status_code == 200, started.text
    run = started.json()["artifact"]["runs"][0]
    assert run["loop_definition_name"] == "Atelier Fast"


def test_reviewed_loop_routes_findings_back_to_implementation(
    app_client: TestClient,
    test_settings: Settings,
    monkeypatch: Any,
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    implementation = _create_agent(app_client, test_settings.workspace_root / "repo")
    reviewer_one = _create_agent(app_client, test_settings.workspace_root / "repo")
    reviewer_two = _create_agent(app_client, test_settings.workspace_root / "repo")
    reviewer_three = _create_agent(app_client, test_settings.workspace_root / "repo")
    reviewer_slugs = [
        str(reviewer_one["slug"]),
        str(reviewer_two["slug"]),
        str(reviewer_three["slug"]),
    ]

    async def fake_launch_reviewer(*_args: Any, **_kwargs: Any) -> str:
        stage = _args[-3]
        if stage.step_id == "implementation":
            return str(implementation["slug"])
        return reviewer_slugs.pop(0)

    monkeypatch.setattr(
        loop_monitor,
        "_launch_or_resume_stage_agent",
        fake_launch_reviewer,
    )
    overlay_payload = app_client.get("/api/loops/atelier-reviewed").json()
    overlay_payload["stages"][0]["agent"]["permissions"] = None
    overlay_response = app_client.post(
        "/api/loops",
        json={
            **overlay_payload,
            "scope": "work",
            "work_slug": "WRK-001",
            "expected_revision": None,
        },
    )
    assert overlay_response.status_code == 201, overlay_response.text
    overlay = overlay_response.json()
    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(notes=_REVIEWED_NOTES),
            "loop_definition_id": overlay["id"],
            "loop_revision": overlay["revision"],
        },
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["artifact"]["runs"][0]["id"]
    first_implementation = _active_stage_agent(app_client, run_id)
    active_records = app_client.app.state.loop_runs.list_active()
    assert len(active_records) == 1
    run_key = active_records[0].run_key
    assert active_records[0].definition_id == "atelier-reviewed"
    assert active_records[0].definition_snapshot["id"] == "atelier-reviewed"

    _append_stage_report(
        app_client,
        str(first_implementation["slug"]),
        outcome="pass",
        summary="Implemented the first pass.",
        validation_evidence="pytest passed",
    )
    reviewing = _wait_stage(app_client, run_id, "code-review")
    review_stage = next(item for item in reviewing["loop_stages"] if item["id"] == "code-review")
    first_reviewer_slug = str(review_stage["agent_slug"])

    _append_stage_report(
        app_client,
        first_reviewer_slug,
        outcome="changes_requested",
        summary="One acceptance gap remains.",
        findings=["Cover the empty-input case."],
    )
    implementing = _wait_stage(app_client, run_id, "implementation")
    assert implementing["loop_pass_number"] == 2
    implementation_stage = next(
        item for item in implementing["loop_stages"] if item["id"] == "implementation"
    )
    assert implementation_stage["attempt"] == 2
    assert implementation_stage["reports"][0]["outcome"] == "pass"
    assert implementation_stage["reports"][0]["summary"] == "Implemented the first pass."
    review_history = next(
        item for item in implementing["loop_stages"] if item["id"] == "code-review"
    )["reports"]
    assert review_history[0]["outcome"] == "changes_requested"
    assert review_history[0]["summary"] == "One acceptance gap remains."
    assert review_history[0]["pass_number"] == 1
    assert review_history[0]["agent_slug"] == first_reviewer_slug
    assert review_history[0]["review_decision"] is None

    _append_stage_report(
        app_client,
        str(implementation_stage["agent_slug"]),
        outcome="pass",
        summary="Added the missing case.",
        validation_evidence="pytest passed",
    )
    reviewing_again = _wait_stage(app_client, run_id, "code-review")
    second_review_stage = next(
        item for item in reviewing_again["loop_stages"] if item["id"] == "code-review"
    )
    assert second_review_stage["agent_slug"] != first_reviewer_slug

    _append_stage_report(
        app_client,
        str(second_review_stage["agent_slug"]),
        outcome="pass",
        summary="Acceptance criteria and diff pass review.",
    )
    awaiting = _wait_run(
        app_client,
        "story-001",
        run_id,
        "completed_pending_review",
        loop_status="awaiting_approval",
    )
    assert awaiting["loop_current_stage_id"] == "approval"

    async def fake_send_loop_prompt(*_args: Any, **_kwargs: Any) -> None:
        return None

    # This test drives reports directly into the durable transcript. The
    # awaiting-approval lifecycle now releases the scripted runtime, so keep
    # the remainder deterministic rather than letting a rebuilt stub answer.
    monkeypatch.setattr(loop_runtime, "send_loop_prompt", fake_send_loop_prompt)

    changes = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/request-changes",
        json={"note": "Clarify the validation evidence before approval."},
    )
    assert changes.status_code == 200, changes.text
    changed_run = changes.json()["artifact"]["runs"][0]
    assert changed_run["loop_current_stage_id"] == "implementation"
    assert changed_run["loop_status"] == "running"
    assert changed_run["loop_pass_number"] == 3

    _append_stage_report(
        app_client,
        str(
            next(item for item in changed_run["loop_stages"] if item["id"] == "implementation")[
                "agent_slug"
            ]
        ),
        outcome="pass",
        summary="Clarified the final validation evidence.",
        validation_evidence="pytest passed with the documented command",
    )
    final_review = _wait_stage(app_client, run_id, "code-review")
    final_reviewer_slug = str(
        next(item for item in final_review["loop_stages"] if item["id"] == "code-review")[
            "agent_slug"
        ]
    )
    _append_stage_report(
        app_client,
        final_reviewer_slug,
        outcome="pass",
        summary="Final evidence and implementation pass review.",
    )
    _wait_run(
        app_client,
        "story-001",
        run_id,
        "completed_pending_review",
        loop_status="awaiting_approval",
    )

    accepted = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/accept",
        json={},
    )
    assert accepted.status_code == 200, accepted.text
    accepted_run = accepted.json()["artifact"]["runs"][0]
    assert accepted_run["status"] == "accepted"
    assert accepted_run["loop_status"] == "accepted"

    cleaned = app_client.post(f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/cleanup")
    assert cleaned.status_code == 200, cleaned.text
    cleaned_run = cleaned.json()["artifact"]["runs"][0]
    assert cleaned_run["loop_status"] == "cleaned"
    assert cleaned_run["cleanup_at"] is not None
    stored = app_client.app.state.loop_runs.get("WRK-001", run_key)
    assert stored is not None
    assert stored.status.value == "cleaned"
    assert stored.cleanup_at is not None
    remaining = {str(agent["slug"]) for agent in app_client.get("/api/works/WRK-001/agents").json()}
    assert {
        str(implementation["slug"]),
        str(reviewer_one["slug"]),
        str(reviewer_two["slug"]),
        str(reviewer_three["slug"]),
    } <= remaining


def test_custom_loop_executes_deterministic_check_stage(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    implementation = _create_agent(app_client, root)
    forked = app_client.post(
        "/api/loops/atelier-fast/fork",
        json={"id": "validated", "name": "Validated"},
    )
    assert forked.status_code == 201, forked.text
    definition = forked.json()
    definition["stages"][0]["transitions"]["pass"] = "validation"
    definition["stages"].insert(
        1,
        {
            "id": "validation",
            "name": "Validation",
            "kind": "deterministic_check",
            "instructions": "",
            "context": [{"kind": "target", "required": True}],
            "agent": None,
            "retry": {"max_attempts": 2, "timeout_minutes": 1},
            "transitions": {
                "pass": "approval",
                "changes_requested": "implementation",
                "blocked_user": "pause",
                "failed": "fail",
            },
            "check_adapter": "command",
            "check_command": [sys.executable, "-c", "print('check passed')"],
        },
    )
    saved = app_client.put(
        "/api/loops/validated",
        json={
            "id": definition["id"],
            "name": definition["name"],
            "description": definition["description"],
            "expected_revision": definition["revision"],
            "forked_from": definition["forked_from"],
            "stages": definition["stages"],
        },
    )
    assert saved.status_code == 200, saved.text
    selected = saved.json()
    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(),
            "agent_slug": implementation["slug"],
            "loop_definition_id": selected["id"],
            "loop_revision": selected["revision"],
        },
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["artifact"]["runs"][0]["id"]
    active_implementation = _active_stage_agent(app_client, run_id)

    _append_stage_report(
        app_client,
        str(active_implementation["slug"]),
        outcome="pass",
        summary="Implementation ready for validation.",
        validation_evidence="unit tests passed",
    )
    awaiting = _wait_run(
        app_client,
        "story-001",
        run_id,
        "completed_pending_review",
        loop_status="awaiting_approval",
    )

    check = next(stage for stage in awaiting["loop_stages"] if stage["id"] == "validation")
    assert check["status"] == "passed"
    assert "check passed" in check["validation_evidence"]


def test_secure_loop_runs_code_and_security_review(
    app_client: TestClient,
    test_settings: Settings,
    monkeypatch: Any,
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    implementation = _create_agent(app_client, root)
    code_reviewer = _create_agent(app_client, root)
    security_reviewer = _create_agent(app_client, root)
    reviewer_slugs = [str(code_reviewer["slug"]), str(security_reviewer["slug"])]

    async def fake_launch_reviewer(*_args: Any, **_kwargs: Any) -> str:
        return reviewer_slugs.pop(0)

    monkeypatch.setattr(
        loop_monitor,
        "_launch_or_resume_stage_agent",
        fake_launch_reviewer,
    )
    secure = builtin_loop_definition("atelier-secure")
    assert secure is not None
    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(notes=_SECURE_NOTES),
            "agent_slug": implementation["slug"],
            "loop_definition_id": secure.definition_id,
            "loop_revision": secure.revision,
        },
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["artifact"]["runs"][0]["id"]
    active_implementation = _active_stage_agent(app_client, run_id)
    _append_stage_report(
        app_client,
        str(active_implementation["slug"]),
        outcome="pass",
        summary="Implementation complete.",
        validation_evidence="pytest passed",
    )
    code_review = _wait_stage(app_client, run_id, "code-review")
    code_stage = next(stage for stage in code_review["loop_stages"] if stage["id"] == "code-review")
    assert code_stage["permissions"] == "read"
    _append_stage_report(
        app_client,
        str(code_stage["agent_slug"]),
        outcome="pass",
        summary="Code review passed.",
    )
    security_review = _wait_stage(app_client, run_id, "security-review")
    security_stage = next(
        stage for stage in security_review["loop_stages"] if stage["id"] == "security-review"
    )
    assert security_stage["agent_slug"] != code_stage["agent_slug"]
    assert security_stage["permissions"] == "read"
    _append_stage_report(
        app_client,
        str(security_stage["agent_slug"]),
        outcome="pass",
        summary="Security review passed.",
    )
    awaiting = _wait_run(
        app_client,
        "story-001",
        run_id,
        "completed_pending_review",
        loop_status="awaiting_approval",
    )
    assert awaiting["loop_current_stage_id"] == "approval"


def test_active_loop_recovers_after_backend_restart(
    test_settings: Settings,
) -> None:
    with TestClient(create_app(test_settings)) as first:
        _create_work(first)
        root = test_settings.workspace_root / "repo"
        _start_plan(first, root)
        assert first.post("/api/works/WRK-001/plan/approve").status_code == 200
        implementation = _create_agent(first, root)
        fast = builtin_loop_definition("atelier-fast")
        assert fast is not None
        started = first.post(
            "/api/works/WRK-001/plan/artifacts/story-001/runs",
            json={
                "brief": _entry_agent_brief(),
                "agent_slug": implementation["slug"],
                "loop_definition_id": fast.definition_id,
                "loop_revision": fast.revision,
            },
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["artifact"]["runs"][0]["id"]
        active_implementation = _active_stage_agent(first, run_id)
        assert len(first.app.state.loop_runs.list_active()) == 1

    first.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        str(active_implementation["slug"]),
        {
            "type": "message_complete",
            "text": _loop_report_text(_complete_report("Completed after restart.")),
        },
    )
    first.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        str(active_implementation["slug"]),
        {"type": "status_change", "status": "idle"},
    )

    with TestClient(create_app(test_settings)) as restarted:
        active = restarted.app.state.loop_runs.list_active()
        assert len(active) == 1
        assert active[0].plan_run_id == run_id
        recovered = _wait_run(
            restarted,
            "story-001",
            run_id,
            "completed_pending_review",
            loop_status="awaiting_approval",
        )
        assert recovered["summary"] == "Completed after restart."


def test_required_loop_context_blocks_before_agent_launch(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    _create_planning_session(app_client, root)
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    forked = app_client.post(
        "/api/loops/atelier-reviewed/fork",
        json={"id": "required-policy", "name": "Required policy"},
    )
    assert forked.status_code == 201, forked.text
    definition = forked.json()
    review = next(stage for stage in definition["stages"] if stage["id"] == "code-review")
    files_context = next(item for item in review["inputs"] if item["kind"] == "files")
    files_context["required"] = True
    files_context["paths"] = ["docs/required-policy.md"]
    saved = app_client.put(
        "/api/loops/required-policy",
        json={
            "id": definition["id"],
            "name": definition["name"],
            "description": definition["description"],
            "expected_revision": definition["revision"],
            "forked_from": definition["forked_from"],
            "stages": definition["stages"],
        },
    )
    assert saved.status_code == 200, saved.text

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(notes=_REVIEWED_NOTES),
            "loop_definition_id": saved.json()["id"],
            "loop_revision": saved.json()["revision"],
        },
    )

    assert started.status_code == 422, started.text
    assert "docs/required-policy.md" in started.json()["detail"]
    assert app_client.get("/api/works/WRK-001/agents").json() == []


def test_selected_loop_launches_a_fresh_agent_briefed_for_the_stage(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    """The Planning session supplies the workspace, the brief the agent.

    It used to supply both, so a story run implemented with whatever model
    happened to write the plan.
    """
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    _create_planning_session(
        app_client,
        root,
        provider="amp",
        model="rush",
        options={"permission_mode": "default"},
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    builtin = app_client.get("/api/loops/atelier-reviewed")
    assert builtin.status_code == 200, builtin.text
    reviewed = app_client.post(
        "/api/loops",
        json={
            **builtin.json(),
            "name": "Work reviewed",
            "scope": "work",
            "work_slug": "WRK-001",
            "expected_revision": None,
            "forked_from": "atelier-reviewed",
        },
    )
    assert reviewed.status_code == 201, reviewed.text
    selected = reviewed.json()

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(notes=_REVIEWED_NOTES),
            "loop_definition_id": selected["id"],
            "loop_revision": selected["revision"],
        },
    )

    assert started.status_code == 200, started.text
    run = started.json()["artifact"]["runs"][0]
    assert run["loop_definition_id"] == selected["id"] == "work-reviewed"
    assert run["loop_definition"] == {
        key: selected[key] for key in ("id", "name", "description", "scope", "revision", "stages")
    }
    assert run["loop_current_stage_id"] == "implementation"
    assert run["agent_slug"]
    agents = app_client.get("/api/works/WRK-001/agents").json()
    launched = next(agent for agent in agents if agent["slug"] == run["agent_slug"])
    assert launched["provider"] == "amp"
    assert launched["model"] == "smart"  # the brief's, not the session's "rush"
    assert launched["folder"] == str(root)
    stored = app_client.app.state.loop_runs.list_active()[0]
    assert stored.definition_snapshot["name"] == "Work reviewed"


def test_selected_loop_pins_optional_planning_brief_note(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    _create_planning_session(
        app_client,
        root,
        provider="amp",
        model="rush",
        options={"permission_mode": "default"},
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    definition = app_client.get("/api/loops/atelier-reviewed").json()
    note = "Keep the source-backed compatibility path intact."

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
            "brief": {
                "goal": "Story run",
                "stages": [
                    {
                        "stage_id": "implementation",
                        "note": note,
                        "agent": {"provider": "amp", "model": "smart"},
                    },
                    {"stage_id": "code-review", "note": note},
                ],
            },
        },
    )

    assert started.status_code == 200, started.text
    run = started.json()["artifact"]["runs"][0]
    assert run["brief_note"] == note
    manifest = app_client.app.state.planningfiles.read_manifest("WRK-001")
    assert manifest is not None
    # The manifest carries run ids; the pinned brief lives in SQL with the
    # rest of the run state.
    assert manifest["artifact_runs"]["story-001"] == [run["id"]]
    stored = next(
        record
        for record in app_client.app.state.loop_runs.list_for_work("WRK-001")
        if record.source is not None and record.source.ref == "story-001"
    )
    pinned = stored.state["brief"]
    assert {stage["stage_id"]: stage["note"] for stage in pinned["stages"]} == {
        "implementation": note,
        "code-review": note,
    }
    prompts = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001",
            run["agent_slug"],
            0,
        )
        if event.get("type") == "user_input"
    ]
    assert any(note in prompt for prompt in prompts)


def test_selected_loop_forks_supplied_agent_for_first_stage_override(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    existing = _create_agent(app_client, root)
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    payload = app_client.get("/api/loops/atelier-fast").json()
    payload["stages"][0]["agent"]["model"] = "rush"
    saved = app_client.post(
        "/api/loops",
        json={
            **payload,
            "scope": "work",
            "work_slug": "WRK-001",
            "expected_revision": None,
        },
    )
    assert saved.status_code == 201, saved.text

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            # Same config the stage pins, so this stays a test about forking
            # rather than about brief-over-definition precedence.
            "brief": _entry_agent_brief(model="rush"),
            "agent_slug": existing["slug"],
            "loop_definition_id": saved.json()["id"],
            "loop_revision": saved.json()["revision"],
        },
    )

    assert started.status_code == 200, started.text
    run_agent = started.json()["artifact"]["runs"][0]["agent_slug"]
    assert run_agent != existing["slug"]
    agents = app_client.get("/api/works/WRK-001/agents").json()
    launched = next(agent for agent in agents if agent["slug"] == run_agent)
    assert launched["model"] == "rush"


def test_selected_loop_preflight_remembers_reused_stage_config(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    _create_planning_session(
        app_client,
        root,
        provider="amp",
        model="smart",
        options={"permission_mode": "default"},
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    payload = app_client.get("/api/loops/atelier-reviewed").json()
    implementation = payload["stages"][0]
    implementation["agent"]["session"] = "reuse"
    implementation["agent"]["model"] = "smart"
    implementation["transitions"]["pass"] = "second-implementation"
    second = json.loads(json.dumps(implementation))
    second.update(id="second-implementation", name="Second implementation")
    second["agent"].update(
        session="fresh",
        provider="codex-acp",
        model="gpt-5.4",
        effort="medium",
    )
    second["transitions"]["pass"] = "code-review"
    payload["stages"].insert(1, second)
    saved = app_client.post(
        "/api/loops",
        json={
            **payload,
            "id": "reuse-config",
            "name": "Reuse config",
            "scope": "work",
            "work_slug": "WRK-001",
            "expected_revision": None,
        },
    )
    assert saved.status_code == 201, saved.text

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(notes=_REVIEWED_NOTES),
            "loop_definition_id": saved.json()["id"],
            "loop_revision": saved.json()["revision"],
        },
    )

    assert started.status_code == 200, started.text


def _plan_with_a_stage_policy(
    app_client: TestClient,
    test_settings: Settings,
    override: dict[str, str],
):
    """Approve a plan whose review stage inherits amp with ``override`` applied."""
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    _create_planning_session(
        app_client,
        root,
        provider="amp",
        model="rush",
        options={"permission_mode": "default"},
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    payload = app_client.get("/api/loops/atelier-reviewed").json()
    review = next(stage for stage in payload["stages"] if stage["id"] == "code-review")
    review["agent"]["provider"] = None
    review["agent"].update(override)
    saved = app_client.post(
        "/api/loops",
        json={
            **payload,
            "scope": "work",
            "work_slug": "WRK-001",
            "expected_revision": None,
        },
    )
    assert saved.status_code == 201, saved.text

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(),
            "loop_definition_id": saved.json()["id"],
            "loop_revision": saved.json()["revision"],
        },
    )

    return started


def test_selected_loop_preflights_a_model_the_inherited_provider_cannot_serve(
    app_client: TestClient, test_settings: Settings
) -> None:
    """A model amp does not have is unrunnable, so Start refuses it up front."""
    started = _plan_with_a_stage_policy(app_client, test_settings, {"model": "gpt-5.5"})

    assert started.status_code == 422, started.text
    assert started.json()["detail"]
    assert app_client.get("/api/works/WRK-001/agents").json() == []
    detail = app_client.get("/api/works/WRK-001/plan/artifacts/story-001").json()
    assert detail["artifact"]["runs"] == []
    assert app_client.app.state.loop_runs.list_active() == []


def test_an_effort_the_inherited_provider_lacks_is_dropped_rather_than_refused(
    app_client: TestClient, test_settings: Settings
) -> None:
    """amp has no effort dial, and an effort it cannot honour is not a reason
    to refuse the run -- the stage is runnable without it. Unlike a model,
    which leaves nothing to run."""
    started = _plan_with_a_stage_policy(app_client, test_settings, {"effort": "high"})

    assert started.status_code == 200, started.text


def test_background_run_monitor_auto_continues_incomplete_report(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"brief": _entry_agent_brief()},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["artifact"]["runs"][0]["id"]
    active_agent = _active_stage_agent(app_client, run_id)
    _publish_agent_event(
        app_client,
        active_agent["slug"],
        {
            "type": "message_complete",
            "ts": "2026-06-29T00:00:00+00:00",
            "text": json.dumps({"summary": "Started the work."}),
        },
    )
    _publish_agent_event(
        app_client,
        active_agent["slug"],
        {"type": "status_change", "status": "idle"},
    )

    deadline = time.monotonic() + 4
    run_state: dict[str, object] | None = None
    while time.monotonic() < deadline:
        run_state = app_client.get(
            f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}"
        ).json()
        if run_state["loop_attempt"] == 2:
            break
        time.sleep(0.05)
    assert run_state is not None
    assert run_state["status"] == "running"
    assert run_state["loop_status"] == "running"
    assert run_state["loop_attempt"] == 2
    user_inputs = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", active_agent["slug"], 0
        )
        if event.get("type") == "user_input"
    ]
    assert "valid report for stage `implementation`" in user_inputs[-1]
    assert "atelier_loop_step_report" in user_inputs[-1]
    assert "validation_evidence" in user_inputs[-1]


def test_failed_selected_loop_stage_retries_with_new_agent_in_same_worktree(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    _create_planning_session(
        app_client,
        root,
        provider="amp",
        model="rush",
        options={"permission_mode": "default"},
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    definition = builtin_loop_definition("atelier-fast")
    assert definition is not None
    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(),
            "loop_definition_id": definition.definition_id,
            "loop_revision": definition.revision,
        },
    )
    assert started.status_code == 200, started.text
    run = started.json()["artifact"]["runs"][0]
    run_id = run["id"]
    agent_slug = run["loop_stages"][0]["agent_slug"]
    assert isinstance(agent_slug, str)
    _wait_for_scripted_agent(app_client, agent_slug)
    _publish_agent_event(
        app_client,
        agent_slug,
        {"type": "error", "message": "Provider stopped."},
    )
    failed = _wait_run(
        app_client,
        "story-001",
        run_id,
        "blocked",
        loop_status="failed",
    )
    worktree = next(
        item["worktree_path"]
        for item in app_client.get("/api/works/WRK-001/agents").json()
        if item["slug"] == agent_slug
    )

    retried = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/retry-stage",
    )

    assert retried.status_code == 200, retried.text
    next_run = retried.json()["artifact"]["runs"][0]
    assert next_run["id"] == failed["id"] == run_id
    assert next_run["loop_status"] == "running"
    retry_agent_slug = next_run["loop_stages"][0]["agent_slug"]
    assert retry_agent_slug != agent_slug
    assert next_run["loop_stages"][0]["attempt"] == 2
    agents = app_client.get("/api/works/WRK-001/agents").json()
    assert len(agents) == 2
    retry_agent = next(item for item in agents if item["slug"] == retry_agent_slug)
    assert retry_agent["worktree_path"] == worktree
    # The retry hint reaches Planning too, now that both surfaces share one
    # retry verb rather than Planning generalizing /resume with a flag.
    prompt = next(
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", retry_agent_slug, 0
        )
        if event.get("type") == "user_input"
    )
    assert "previous attempt" in prompt
    assert "git status" in prompt


def _failed_plan_run_for_retry(
    app_client: TestClient, test_settings: Settings
) -> tuple[str, str]:
    """Drive a story run to a retryable failure; return (run_id, agent_slug)."""
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _start_plan(app_client, root)
    _create_planning_session(
        app_client,
        root,
        provider="amp",
        model="rush",
        options={"permission_mode": "default"},
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    definition = builtin_loop_definition("atelier-fast")
    assert definition is not None
    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(),
            "loop_definition_id": definition.definition_id,
            "loop_revision": definition.revision,
        },
    )
    assert started.status_code == 200, started.text
    run = started.json()["artifact"]["runs"][0]
    agent_slug = run["loop_stages"][0]["agent_slug"]
    assert isinstance(agent_slug, str)
    _wait_for_scripted_agent(app_client, agent_slug)
    _publish_agent_event(
        app_client, agent_slug, {"type": "error", "message": "Provider stopped."}
    )
    _wait_run(app_client, "story-001", run["id"], "blocked", loop_status="failed")
    return run["id"], agent_slug


def test_plan_run_stage_retry_applies_same_provider_model_override(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    """The model/effort override works on Planning runs, not just standalone."""
    run_id, agent_slug = _failed_plan_run_for_retry(app_client, test_settings)

    # amp has no reasoning-effort dial: rejected cleanly, run stays retryable.
    rejected = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/retry-stage",
        json={"effort": "high"},
    )
    assert rejected.status_code == 422, rejected.text

    retried = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/retry-stage",
        json={"model": "smart"},
    )
    assert retried.status_code == 200, retried.text
    retry_agent_slug = retried.json()["artifact"]["runs"][0]["loop_stages"][0]["agent_slug"]
    assert retry_agent_slug != agent_slug
    retry_agent = next(
        item
        for item in app_client.get("/api/works/WRK-001/agents").json()
        if item["slug"] == retry_agent_slug
    )
    assert retry_agent["model"] == "smart"  # overridden from the run's "rush"


def test_resume_rejects_non_blocked_run(app_client: TestClient, test_settings: Settings) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"brief": _entry_agent_brief()},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["artifact"]["runs"][0]["id"]

    resumed = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/resume",
        json={"brief": _entry_agent_brief()},
    )

    assert resumed.status_code == 422, resumed.text


def test_blocker_artifact_report_blocks_for_user(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)

    _append_agent_loop_report(
        app_client,
        str(agent["slug"]),
        {
            **_complete_report("Could not finish."),
            "outcome": "blocked_user",
            "blocker": "Need the user to choose an API contract.",
        },
    )
    run_state = _wait_run(
        app_client,
        "story-001",
        run_id,
        "blocked",
        loop_status="blocked_user",
    )

    assert run_state["status"] == "blocked"
    assert run_state["loop_status"] == "blocked_user"
    assert run_state["loop_status_reason"] == "Need the user to choose an API contract."


def test_blocked_artifact_loop_resumes_when_user_marks_resolved(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)
    _append_agent_loop_report(
        app_client,
        str(agent["slug"]),
        {
            **_complete_report("Could not finish."),
            "outcome": "blocked_user",
            "blocker": "Need the user to choose an API contract.",
        },
    )
    _wait_run(
        app_client,
        "story-001",
        run_id,
        "blocked",
        loop_status="blocked_user",
    )

    resumed = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/resume",
        json={
            "resolution_note": "Use the v2 API contract.",
        },
    )

    assert resumed.status_code == 200, resumed.text
    run_state = resumed.json()["artifact"]["runs"][0]
    assert run_state["status"] == "running"
    assert run_state["loop_status"] == "running"
    assert run_state["loop_attempt"] == 2
    user_inputs = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", agent["slug"], 0
        )
        if event.get("type") == "user_input"
    ]
    assert "Use the v2 API contract." in user_inputs[-1]


def test_artifact_proposal_accepts_full_source_update(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    detail = app_client.get("/api/works/WRK-001/plan/artifacts/story-001").json()

    proposed = detail["content"] + "\n## Implementation Notes\n\nUse the diff review.\n"
    created = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/proposals",
        json={"title": "Add implementation notes", "proposed_content": proposed},
    )

    assert created.status_code == 200, created.text
    proposal = created.json()["artifact"]["proposals"][0]
    assert proposal["status"] == "pending"
    assert proposal["source_hash"] == detail["artifact"]["source_hash"]

    accepted = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/proposals/{proposal['id']}/accept"
    )

    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["artifact"]["proposals"][0]["status"] == "accepted"
    assert "Use the diff review." in body["content"]
    assert _planning_file(test_settings, "stories/story-001.md").read_text() == proposed


def test_stale_artifact_proposal_is_rejected(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    detail = app_client.get("/api/works/WRK-001/plan/artifacts/story-001").json()
    created = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/proposals",
        json={"title": "Stale", "proposed_content": detail["content"] + "\nProposal\n"},
    )
    proposal = created.json()["artifact"]["proposals"][0]
    story = _planning_file(test_settings, "stories/story-001.md")
    story.write_text(story.read_text() + "\nManual edit.\n")

    accepted = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/proposals/{proposal['id']}/accept"
    )

    assert accepted.status_code == 409


def test_dependency_blocks_launch_until_dependency_is_reviewable(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    story_002 = _planning_file(test_settings, "stories/story-002.md")
    story_002.write_text(
        """# Story 002: Follow-up

## Scope

Do the dependent work.

## Acceptance Criteria

- Dependent work is complete.

## Dependencies

- story-001
"""
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    blocked = app_client.get("/api/works/WRK-001/plan").json()
    story = next(a for a in blocked["artifacts"] if a["id"] == "story-002")
    assert story["launchable"] is False
    assert story["dependencies"] == ["story-001"]
    assert story["launch_blockers"] == ["Dependency story-001 is not complete or ready for review."]

    agent, run_id = _start_artifact_run(app_client, test_settings)
    _complete_artifact_run(
        app_client,
        str(agent["slug"]),
        run_id,
        summary="Ready for dependent work.",
    )
    ready = app_client.get("/api/works/WRK-001/plan").json()
    story = next(a for a in ready["artifacts"] if a["id"] == "story-002")
    assert story["launchable"] is True
    assert story["launch_blockers"] == []


def test_tracking_blocker_gates_launch_until_referenced_artifact_is_reviewable(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    story_002 = _planning_file(test_settings, "stories/story-002.md")
    story_002.write_text(
        """# Story 002: Blocked Follow-up

## Scope

Do follow-up work.

## Acceptance Criteria

- Follow-up is complete.

## Dependencies

- None
"""
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    linked = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-002/tracking",
        json={"kind": "blocker", "title": "Wait for story 001", "ref": "story-001"},
    )

    assert linked.status_code == 200, linked.text
    body = linked.json()["artifact"]
    assert body["tracking"][0]["kind"] == "blocker"
    assert body["launchable"] is False
    assert body["launch_blockers"] == [
        "Blocked by story-001 until it is complete or ready for review."
    ]

    agent, run_id = _start_artifact_run(app_client, test_settings)
    _complete_artifact_run(
        app_client,
        str(agent["slug"]),
        run_id,
        summary="Ready for review.",
    )
    ready = app_client.get("/api/works/WRK-001/plan").json()
    story = next(a for a in ready["artifacts"] if a["id"] == "story-002")
    assert story["launchable"] is True


def test_tracking_links_and_bug_findings_are_attached_to_artifact(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")

    jira = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/tracking",
        json={
            "kind": "jira",
            "title": "AT-123",
            "url": "https://jira.example/browse/AT-123",
            "status": "todo",
        },
    )
    assert jira.status_code == 200, jira.text

    bug = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/bugs",
        json={"title": "Review regression", "description": "Found during review."},
    )

    assert bug.status_code == 200, bug.text
    body = bug.json()["artifact"]
    assert [link["kind"] for link in body["tracking"]] == ["jira", "bug"]
    assert body["tracking"][1]["ref"] == "bug-001"
    bug_doc = _planning_file(test_settings, "bugs/bug-001.md").read_text()
    assert "## Explanation" in bug_doc
    assert "## References" in bug_doc
    assert "Found during review." in bug_doc


def test_artifact_run_cleanup_is_recorded(app_client: TestClient, test_settings: Settings) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)
    _complete_artifact_run(app_client, str(agent["slug"]), run_id, summary="Done.")
    accepted = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/accept",
        json={"summary": "Done."},
    )
    assert accepted.status_code == 200, accepted.text
    agent_slug = str(agent["slug"])
    worktree = Path(str(agent["worktree_path"]))
    transcript_before = list(
        app_client.app.state.workstore.read_transcript_from_cursor("WRK-001", agent_slug, 0)
    )

    cleaned = app_client.post(f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/cleanup")

    assert cleaned.status_code == 200, cleaned.text
    cleaned_run = cleaned.json()["artifact"]["runs"][0]
    assert cleaned_run["status"] == "accepted"
    assert cleaned_run["loop_status"] == "cleaned"
    assert cleaned_run["cleanup_at"] is not None
    assert agent_slug in {
        str(item["slug"]) for item in app_client.get("/api/works/WRK-001/agents").json()
    }
    assert worktree.exists()
    assert (
        list(app_client.app.state.workstore.read_transcript_from_cursor("WRK-001", agent_slug, 0))[
            : len(transcript_before)
        ]
        == transcript_before
    )


def test_artifact_run_can_be_cancelled(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)
    workspace = Path(str(agent["worktree_path"]))

    cancelled = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/cancel"
    )

    assert cancelled.status_code == 200, cancelled.text
    run = cancelled.json()["artifact"]["runs"][0]
    assert run["status"] == "needs_attention"
    assert run["loop_status"] == "cancelled"
    assert {stage["status"] for stage in run["loop_stages"]} == {"cancelled"}
    assert workspace.exists()
    stored = app_client.app.state.loop_runs.get(
        "WRK-001",
        f"loop-story-001-{run_id}",
    )
    assert stored is not None
    assert stored.status.value == "cancelled"


def test_plan_materialization_status_reports_pending_permission(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    root.mkdir(parents=True, exist_ok=True)
    chat_slug = _create_materializer_chat(app_client, root)
    app_client.app.state.chatstore.append_transcript_event_with_seq(
        chat_slug,
        {
            "type": "permission_request",
            "ts": "2026-06-29T00:00:00+00:00",
            "request_id": "mat-perm-1",
            "tool_id": "tool-1",
            "tool_name": "Bash",
            "title": "Inspect files",
            "tool_input": {"command": "rg planning frontend/src"},
        },
    )

    res = app_client.get("/api/works/WRK-001/plan/materialization-status")

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["state"] == "waiting_permission"
    assert data["chat_slug"] == chat_slug
    assert data["last_event_type"] == "permission_request"
    assert data["last_event_summary"] == "Inspect files"
    assert data["message"] == "Inspect files"
    assert data["tool_name"] == "Bash"
    assert len(data["pending_permissions"]) == 1
    pending = data["pending_permissions"][0]
    assert pending["request_id"] == "mat-perm-1"
    assert pending["tool_name"] == "Bash"
    assert pending["tool_input"] == {"command": "rg planning frontend/src"}
    assert pending["ts"] == "2026-06-29T00:00:00+00:00"
    assert pending["seq"] > 0
    assert pending["options"] == []
    assert data["recent_activity"][-1]["text"] == "Inspect files"

    supervisor = _FakeMaterializerSupervisor()
    app_client.app.dependency_overrides[get_chat_supervisor] = lambda: supervisor
    try:
        decision = app_client.post(
            "/api/works/WRK-001/plan/materialization-permission",
            json={"request_id": "mat-perm-1", "decision": "allow"},
        )
    finally:
        app_client.app.dependency_overrides.pop(get_chat_supervisor, None)

    assert decision.status_code == 204, decision.text
    assert supervisor.permissions == [("mat-perm-1", "allow")]


def _create_agent(client: TestClient, folder: Path) -> dict[str, object]:
    folder.mkdir(parents=True, exist_ok=True)
    res = client.post(
        "/api/works/WRK-001/agents",
        json={
            "name": "Planner Dev",
            "persona": "developer",
            "role": "Implement the artifact",
            "provider": "amp",
            "model": "smart",
            "folder": str(folder),
            "options": {},
            "contexts": [],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_materializer_chat(
    client: TestClient,
    root: Path,
    *,
    provider: str = "codex",
    model: str = "gpt-5.4",
    options: dict[str, object] | None = None,
) -> str:
    record = client.app.state.chatstore.create_chat(
        CreateChatRequest(
            provider=provider,
            model=model,
            first_message="Materialize an Atelier source-backed plan.",
            title="Planning materializer",
            grounding=ChatGrounding(kind="work", ref="WRK-001"),
            working_directory=str(root),
            options=options or {"sandbox": "workspace-write", "approval_mode": "never"},
        )
    )
    assert record.chat.slug is not None
    return record.chat.slug


def _create_planning_session(
    client: TestClient,
    root: Path,
    *,
    framework: PlanningFramework = "bmad",
    profile: PlanningProfile = "feature",
    provider: str = "codex",
    model: str = "gpt-5.4",
    options: dict[str, object] | None = None,
    planning_chat_slug: str | None = None,
    plan_artifacts_dir: str | None = None,
) -> None:
    now = datetime.now(UTC)
    client.app.state.planning_sessions.upsert_session(
        PlanningSession(
            work_slug="WRK-001",
            planning_chat_slug=planning_chat_slug,
            root_path=str(root),
            plan_artifacts_dir=(
                plan_artifacts_dir or default_plan_artifacts_dir(framework, "WRK-001")
            ),
            framework=framework,
            profile=profile,
            provider=provider,  # type: ignore[arg-type]
            model=model,
            options=options or {},
            created_at=now,
            updated_at=now,
        )
    )


class _FakeMaterializerSupervisor:
    """Capture materializer permissions and follow-up prompts."""

    def __init__(self, chatstore: Any | None = None) -> None:
        self._chatstore = chatstore
        self.permissions: list[tuple[str, str]] = []
        self.inputs: list[str] = []
        self.stopped: list[str] = []

    def is_registered(self, _chat_slug: str) -> bool:
        return True

    def is_lazy_registered(self, _chat_slug: str) -> bool:
        return False

    async def resolve_permission(self, _chat_slug: str, request_id: str, decision: str) -> None:
        self.permissions.append((request_id, decision))

    async def send_input(
        self, chat_slug: str, text: str, *, record_user_input: bool = True
    ) -> None:
        self.inputs.append(text)
        if self._chatstore is not None and record_user_input:
            self._chatstore.append_transcript_event_with_seq(
                chat_slug,
                {
                    "type": "user_input",
                    "ts": "2026-06-29T00:00:01+00:00",
                    "text": text,
                },
            )

    async def stop_agent(self, chat_slug: str) -> None:
        self.stopped.append(chat_slug)


def _write_materializer_report(
    chatstore: Any,
    chat_slug: str,
    artifact_dir: Path,
) -> None:
    stories = artifact_dir / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    (stories / "story-001.md").write_text(
        "# Story 001\n\n## Acceptance Criteria\n\n- Plan exists.\n"
    )
    report = {
        "atelier_plan_materialization": {
            "artifacts": [
                {
                    "path": "stories/story-001.md",
                    "title": "Story 001",
                    "artifact_kind": "story",
                    "executable": True,
                    "dependencies": [],
                }
            ]
        }
    }
    chatstore.append_transcript_event_with_seq(
        chat_slug,
        {
            "type": "message_complete",
            "ts": "2026-06-29T00:00:02+00:00",
            "text": json.dumps(report),
        },
    )


def _planning_file(settings: Settings, rel_path: str) -> Path:
    return _artifact_root(settings.workspace_root / "repo") / rel_path


def _artifact_root(
    root: Path, framework: PlanningFramework = "bmad", work_slug: str = "WRK-001"
) -> Path:
    return root / default_plan_artifacts_dir(framework, work_slug)


def test_story_runs_share_one_worktree_per_story(
    app_client: TestClient, test_settings: Settings
) -> None:
    """The worktree slug is derived from the story, not the launching agent.

    A follow-up run has to land on the same branch for "an open pull request
    is updated in place" to hold. Defaulting to the agent slug gave every run
    its own worktree, so a second run would fork a fresh branch and open a
    second PR.

    Needs a real git root: WorktreeManager.ensure hands back the source
    folder unchanged when it is not a repository, so a non-git root proves
    nothing about slugs.
    """
    _create_work(app_client)
    root = test_settings.workspace_root / "repo"
    _init_git_repo(root)
    _start_plan(app_client, root)
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    agent, _run_id = _start_artifact_run(app_client, test_settings)

    worktree = Path(str(agent["worktree_path"]))
    assert worktree.name == "loop-story-001"
    assert worktree.name != agent["slug"]


def _init_git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("test repository\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)


def test_manifest_holds_run_ids_not_run_state(
    app_client: TestClient, test_settings: Settings
) -> None:
    """SQL is canonical for run state; the manifest stays a plan description.

    The manifest lives in the user's repository. Run state is machine state
    -- large, and rewritten on every monitor tick -- so the plan file keeps
    only enough to show which stories have been run.
    """
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    _agent, run_id = _start_artifact_run(app_client, test_settings)

    manifest = app_client.app.state.planningfiles.read_manifest("WRK-001")
    assert manifest is not None

    assert manifest["artifact_runs"]["story-001"] == [run_id]
    assert "loop" not in json.dumps(manifest["artifact_runs"])

    # The projection still returns the full run, sourced from SQL.
    runs = app_client.get(
        "/api/works/WRK-001/plan/artifacts/story-001/runs"
    ).json()
    assert [item["id"] for item in runs] == [run_id]
    assert runs[0]["loop_stages"]


def _accept_story_run(client: TestClient, agent_slug: str, run_id: str) -> None:
    _complete_artifact_run(client, agent_slug, run_id)
    accepted = client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/accept",
        json={"summary": "Accepted for the follow-up."},
    )
    assert accepted.status_code == 200, accepted.text


def test_a_follow_up_continues_a_run_whose_stored_revision_no_longer_matches(
    app_client: TestClient, test_settings: Settings, isolated_engine: Engine
) -> None:
    """A follow-up continues the loop the run actually executed.

    The stored revision is a pin, not a staleness check. It stops matching for
    reasons that have nothing to do with the loop's content -- a field leaving
    the hashed payload moves every hash at once -- so an upgrade must not
    refuse follow-ups on runs nobody touched.
    """
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)
    _accept_story_run(app_client, str(agent["slug"]), run_id)
    with isolated_engine.begin() as connection:
        rows = connection.execute(text("SELECT id, state FROM loop_runs")).fetchall()
        restamped = 0
        for row_id, state in rows:
            blob = json.loads(state) if isinstance(state, str) else dict(state)
            loop_state = blob.get("loop")
            if not isinstance(loop_state, dict) or not loop_state.get("definition_revision"):
                continue
            loop_state["definition_revision"] = "0ldrev"
            # The snapshot's own revision moves with it: both are written from
            # the same value, so restamping one alone fabricates a state no
            # build produces and the revision check rightly rejects it.
            snapshot = loop_state.get("definition_snapshot")
            if isinstance(snapshot, dict):
                snapshot["revision"] = "0ldrev"
            connection.execute(
                text("UPDATE loop_runs SET state = :state, definition_revision = '0ldrev'"
                     " WHERE id = :id"),
                {"state": json.dumps(blob), "id": row_id},
            )
            restamped += 1
    assert restamped >= 1, "expected a stored run revision to restamp"

    res = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/rerun",
        json={"kind": "amend", "note": "still fine after the hash moved"},
    )

    assert res.status_code == 201, res.text


def test_story_follow_up_amend_reenters_the_task_stage(
    app_client: TestClient, test_settings: Settings
) -> None:
    """Apply feedback: a new run carrying the note, entering where it always does."""
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)
    _accept_story_run(app_client, str(agent["slug"]), run_id)

    res = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/rerun",
        json={"kind": "amend", "note": "the reviewer wants Y handled"},
    )

    assert res.status_code == 201, res.text
    runs = res.json()["artifact"]["runs"]
    assert [item["id"] for item in runs] == [run_id, "run-002"]
    follow_up = runs[-1]
    assert follow_up["loop_current_stage_id"] == "implementation"
    assert "the reviewer wants Y handled" in follow_up["brief_note"]


def test_story_follow_up_verify_skips_the_task_stages(
    app_client: TestClient, test_settings: Settings
) -> None:
    """Verify current state: enters at the first review, task stages skipped."""
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "loop_definition_id": "atelier-reviewed",
            "brief": _entry_agent_brief(notes=_REVIEWED_NOTES),
        },
    )
    assert started.status_code == 200, started.text
    run_id = str(started.json()["artifact"]["runs"][-1]["id"])

    implementer = _active_stage_agent(app_client, run_id)
    _append_stage_report(
        app_client,
        str(implementer["slug"]),
        outcome="pass",
        summary="Implemented it.",
        validation_evidence="pytest passed",
    )
    reviewing = _wait_stage(app_client, run_id, "code-review")
    reviewer = next(
        item for item in reviewing["loop_stages"] if item["id"] == "code-review"
    )
    _append_stage_report(
        app_client,
        str(reviewer["agent_slug"]),
        outcome="pass",
        summary="Looks right.",
    )
    _wait_run(
        app_client,
        "story-001",
        run_id,
        "completed_pending_review",
        loop_status="awaiting_approval",
    )
    accepted = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/accept",
        json={"summary": "Accepted for the follow-up."},
    )
    assert accepted.status_code == 200, accepted.text

    res = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/rerun",
        json={"kind": "verify"},
    )

    assert res.status_code == 201, res.text
    follow_up = res.json()["artifact"]["runs"][-1]
    stages = {item["id"]: item["status"] for item in follow_up["loop_stages"]}
    assert stages["implementation"] == "skipped"
    assert follow_up["loop_current_stage_id"] == "code-review"


def test_story_follow_up_verify_needs_something_to_verify_with(
    app_client: TestClient, test_settings: Settings
) -> None:
    """atelier-fast has no review or check stage, so Verify is unavailable."""
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)
    _accept_story_run(app_client, str(agent["slug"]), run_id)

    res = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/rerun",
        json={"kind": "verify"},
    )

    assert res.status_code == 422, res.text
    assert "review or check stage" in res.json()["detail"]


def test_story_follow_up_rejects_a_run_that_has_not_finished(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    _agent, run_id = _start_artifact_run(app_client, test_settings)

    res = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/rerun",
        json={"kind": "amend", "note": "too early"},
    )

    assert res.status_code == 422, res.text
    assert "cannot be reused" in res.json()["detail"]


def test_story_run_pins_a_full_brief_from_the_setup_screen(
    app_client: TestClient, test_settings: Settings
) -> None:
    """Planning starts a run from the same brief shape a Loop-mode run does.

    Before this, the story route accepted only `brief_note` -- one string
    applied to every agent stage -- so per-stage notes and provider
    overrides configured in setup had nowhere to go.
    """
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "loop_definition_id": "atelier-reviewed",
            "brief": {
                "goal": "Ship the picker",
                "stages": [
                    {
                        "stage_id": "implementation",
                        "note": "keep the public API",
                        "agent": {"provider": "amp", "model": "smart"},
                    },
                    {"stage_id": "code-review", "note": "focus on concurrency"},
                ],
            },
        },
    )

    assert started.status_code == 200, started.text
    run_id = str(started.json()["artifact"]["runs"][-1]["id"])
    stored = next(
        record
        for record in app_client.app.state.loop_runs.list_for_work("WRK-001")
        if record.plan_run_id == run_id
    )
    pinned = stored.state["brief"]
    assert {stage["stage_id"]: stage["note"] for stage in pinned["stages"]} == {
        "implementation": "keep the public API",
        "code-review": "focus on concurrency",
    }


def test_story_run_requires_a_provider_on_its_entry_stage(
    app_client: TestClient, test_settings: Settings
) -> None:
    """A story has no parent agent, so the loop's entry stage must name one.

    It used to fall back to the Planning session's provider -- the agent that
    wrote the plan, which is a coincidence rather than a choice, and usually
    the wrong model to implement with.
    """
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    res = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"loop_definition_id": "atelier-fast"},
    )

    assert res.status_code == 422, res.text
    assert "does not pin a provider" in res.json()["detail"]


def test_story_run_launches_the_agent_the_brief_pinned(
    app_client: TestClient, test_settings: Settings
) -> None:
    """The entry stage runs the brief's agent, not the Planning session's.

    Regression: the brief reached the launch and was read only for command
    prefixes, so the run silently inherited the agent that *wrote* the plan.
    The 422 guard above made it invisible -- it accepts the run *because* of
    the override that was then dropped. Assert the launched agent, not the
    status code.
    """
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    _create_planning_session(
        app_client,
        test_settings.workspace_root / "repo",
        provider="amp",
        model="rush",
        options={"permission_mode": "default"},
    )
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": {
                "goal": "Story run",
                "stages": [
                    {
                        "stage_id": "implementation",
                        "agent": {
                            "provider": "amp",
                            "model": "deep",
                            "options": {"permission_mode": "allow_all"},
                        },
                    }
                ],
            }
        },
    )
    assert started.status_code == 200, started.text

    run_id = str(started.json()["artifact"]["runs"][0]["id"])
    agent = _active_stage_agent(app_client, run_id)

    assert agent["provider"] == "amp"
    assert agent["model"] == "deep"
    assert agent["options"]["permission_mode"] == "allow_all"


@pytest.mark.parametrize(
    ("entry_extra", "stages", "detail"),
    [
        (
            {},
            [{"stage_id": "made-up", "note": "hi"}],
            "loop brief references unknown stage: made-up",
        ),
        (
            {"context": [{"kind": "note", "value": "  "}]},
            [],
            "loop brief context is empty: implementation",
        ),
        (
            {},
            [{"stage_id": "approval", "note": "hi"}],
            "loop brief stage does not accept input: approval",
        ),
        (
            {},
            [{"stage_id": "implementation", "note": "twice"}],
            "loop brief repeats stage: implementation",
        ),
    ],
    ids=["unknown-stage", "empty-context", "not-agent-backed", "repeated-stage"],
)
def test_story_run_rejects_a_brief_that_does_not_fit_the_loop(
    app_client: TestClient,
    test_settings: Settings,
    entry_extra: dict,
    stages: list[dict],
    detail: str,
) -> None:
    """The story path validates the brief the goal-driven path already did.

    It used to accept anything and honour only the parts it happened to read.
    """
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    res = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": {
                "goal": "Story run",
                "stages": [
                    {
                        "stage_id": "implementation",
                        "agent": {"provider": "amp", "model": "smart"},
                        **entry_extra,
                    },
                    *stages,
                ],
            }
        },
    )

    assert res.status_code == 422, res.text
    assert detail in res.json()["detail"]
    assert app_client.get("/api/works/WRK-001/agents").json() == []


def _reviewed_with_required_note(app_client: TestClient) -> dict:
    """A WRK-001 fork of Atelier Reviewed whose review stage demands a note.

    The built-ins no longer declare ``note_required`` -- a reviewer already
    gets the goal, the target and the diff -- so the contract is exercised
    through a definition that opts into it, which is who it is for.
    """
    payload = app_client.get("/api/loops/atelier-reviewed").json()
    payload["stages"][0]["agent"]["provider"] = "amp"
    payload["stages"][0]["agent"]["model"] = "smart"
    review = next(stage for stage in payload["stages"] if stage["id"] == "code-review")
    review["note_required"] = True
    saved = app_client.post(
        "/api/loops",
        json={**payload, "scope": "work", "work_slug": "WRK-001", "expected_revision": None},
    )
    assert saved.status_code == 201, saved.text
    return saved.json()


def test_story_run_rejects_a_brief_missing_a_required_note(
    app_client: TestClient, test_settings: Settings
) -> None:
    """`note_required` is the definition author's contract, not a suggestion."""
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    definition = _reviewed_with_required_note(app_client)

    res = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief": _entry_agent_brief(),
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
        },
    )

    assert res.status_code == 422, res.text
    assert "Required loop brief is missing for: Code review" in res.json()["detail"]


def test_story_run_shorthand_still_fills_required_notes_for_the_client(
    app_client: TestClient, test_settings: Settings
) -> None:
    """`brief_note` predates briefs, so Atelier fills the slots it declared.

    Same split the goal-driven path makes: a brief the client sent is held to
    the loop's contract, a brief Atelier synthesised is completed for it.
    """
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    payload = app_client.get("/api/loops/atelier-reviewed").json()
    payload["stages"][0]["agent"]["provider"] = "amp"
    payload["stages"][0]["agent"]["model"] = "smart"
    saved = app_client.post(
        "/api/loops",
        json={
            **payload,
            "scope": "work",
            "work_slug": "WRK-001",
            "expected_revision": None,
        },
    )
    assert saved.status_code == 201, saved.text

    res = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "brief_note": "keep the public API",
            "loop_definition_id": saved.json()["id"],
            "loop_revision": saved.json()["revision"],
        },
    )

    assert res.status_code == 200, res.text


def test_a_run_started_without_a_brief_still_gets_one(
    app_client: TestClient, test_settings: Settings
) -> None:
    """Every run carries a brief, so the loop's contract always applies.

    A story run may omit the brief entirely when the definition pins the
    entry provider itself. That used to skip validation and leave the run
    with no brief to inherit from, so `note_required` was enforced against
    callers who sent a brief and ignored for callers who sent none.
    """
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    definition = _reviewed_with_required_note(app_client)

    started = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
        },
    )

    assert started.status_code == 200, started.text
    run = started.json()["artifact"]["runs"][0]
    assert run["brief"] is not None
    assert run["brief"]["goal"]
    # The required review slot was filled for the client rather than refused.
    filled = {stage["stage_id"] for stage in run["brief"]["stages"] if stage["note"]}
    assert "code-review" in filled


def test_story_follow_up_inherits_the_provider_of_the_run_it_continues(
    app_client: TestClient, test_settings: Settings
) -> None:
    """A follow-up's parent is that run, so it need not re-pin the provider."""
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    agent, run_id = _start_artifact_run(app_client, test_settings)
    _accept_story_run(app_client, str(agent["slug"]), run_id)

    res = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{run_id}/rerun",
        json={"kind": "amend", "note": "apply the review"},
    )

    assert res.status_code == 201, res.text
    follow_up = res.json()["artifact"]["runs"][-1]
    started = next(
        item
        for item in app_client.get("/api/works/WRK-001/agents").json()
        if item["slug"] == follow_up["agent_slug"]
    )
    assert started["provider"] == agent["provider"]
    assert started["model"] == agent["model"]
