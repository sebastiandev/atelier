"""Integration tests for source-backed Work planning routes."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.domain.chats import runtime as chat_runtime
from src.domain.chatstore.dtos import ChatGrounding, CreateChatRequest
from src.domain.commands.planning import materialize, submit_materialization
from src.domain.planning.dtos import (
    PlanArtifactEntry,
    PlanningFramework,
    PlanningProfile,
)
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
    return res.json()


def _complete_report(summary: str = "Implemented the artifact.") -> dict[str, str]:
    return {
        "summary": summary,
        "divergences": "None.",
        "skipped_scope": "None.",
        "blockers": "None.",
        "decisions": "Used the planned approach.",
        "changes": "Updated the assigned artifact implementation.",
        "validation_evidence": "pytest passed",
    }


def _write_default_plan_sources(root: Path) -> list[dict[str, object]]:
    planning_dir = root / ".atelier" / "planning" / "WRK-001"
    planning_dir.mkdir(parents=True, exist_ok=True)
    (planning_dir / "intent.md").write_text(
        "# Intent\n\n## Objective\n\nMake work planning source-backed.\n"
    )
    (planning_dir / "design-guide.md").write_text(
        "# Design Guide\n\n## Validation\n\n- Run tests.\n"
    )
    stories = planning_dir / "stories"
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
) -> Any:
    submit_materialization.execute(
        client.app.state.workstore,
        client.app.state.planningfiles,
        submit_materialization.SubmitPlanMaterializationRequest(
            work_slug="WRK-001",
            root_path=str(root),
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

    planning_dir = repo_root / ".atelier" / "planning" / "WRK-001"
    assert body["work_slug"] == "WRK-001"
    assert body["root_path"] == str(repo_root)
    assert body["planning_path"] == str(planning_dir)
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
    assert (planning_dir / "intent.md").exists()
    assert (planning_dir / "design-guide.md").exists()
    assert not (planning_dir / "design-guidance.md").exists()
    assert (planning_dir / "stories" / "story-001.md").exists()
    assert not (test_settings.workspace_root / ".atelier" / "planning" / "WRK-001").exists()
    manifest = json.loads((planning_dir / "manifest.json").read_text())
    assert [
        (item["path"], item["artifact_kind"], item["executable"])
        for item in manifest["artifacts"]
    ] == [
        ("intent.md", "brief", False),
        ("design-guide.md", "architecture", False),
        ("stories/story-001.md", "story", True),
    ]
    assert all("content" not in item for item in manifest["artifacts"])


def test_start_plan_records_framework_and_profile_depth(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repos" / "planner"
    _mark_framework_ready(repo_root, "spec")
    planning_dir = repo_root / ".atelier" / "planning" / "WRK-001"
    planning_dir.mkdir(parents=True, exist_ok=True)
    (planning_dir / "spec.md").write_text("# Outage Spec\n")
    (planning_dir / "scenarios.md").write_text("# Outage Scenarios\n")
    (planning_dir / "acceptance.md").write_text("# Outage Acceptance\n")
    (planning_dir / "tasks").mkdir()
    (planning_dir / "tasks" / "task-001.md").write_text(
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
    manifest_path = repo_root / ".atelier" / "planning" / "WRK-001" / "manifest.json"
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
    planning_dir = repo_root / ".atelier" / "planning" / "WRK-001"
    planning_dir.mkdir(parents=True, exist_ok=True)
    (planning_dir / "spec.md").write_text("# Import Spec\n\n## Behavior\n\nImport CSV.\n")
    (planning_dir / "tasks").mkdir()
    (planning_dir / "tasks" / "task-001.md").write_text(
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


def test_start_plan_rejects_missing_files(
    app_client: TestClient, test_settings: Settings
) -> None:
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
            "framework": "bmad",
            "profile": "feature",
            "provider": "codex",
            "model": "gpt-5.4",
            "options": {},
        },
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["title"] == "Planning"
    assert body["grounding"] == {"kind": "work", "ref": "WRK-001"}
    assert body["working_directory"] == str(repo_root)
    first = body["transcript"][0]["body"]
    assert "Initialize an Atelier Planning session." in first
    assert "- Framework: BMAD" in first
    assert "- User idea: Plan a CSV import feature." in first


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
    planning_dir = repo_root / ".atelier" / "planning" / "WRK-001"
    planning_dir.mkdir(parents=True, exist_ok=True)
    (planning_dir / "spec.md").write_text("# Import Spec\n")
    (planning_dir / "tasks").mkdir()
    (planning_dir / "tasks" / "task-001.md").write_text(
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
        json={
            "root_path": str(repo_root),
            "framework": "spec",
            "profile": "feature",
            "provider": "codex",
            "model": "gpt-5.4",
            "options": {},
        },
    )

    assert finalized.status_code == 200, finalized.text
    body = finalized.json()
    assert body["materialization_status"]["state"] == "complete"
    plan = body["plan"]
    assert plan["framework"] == "spec"
    assert plan["phase"] == "planned"
    assert {artifact["id"] for artifact in plan["artifacts"]} == {
        "spec",
        "task-001",
    }
    task = next(a for a in plan["artifacts"] if a["id"] == "task-001")
    assert task["dependencies"] == ["spec"]


def test_start_plan_requires_agent_config(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")

    finalized = app_client.post(
        "/api/works/WRK-001/plan",
        json={
            "root_path": str(repo_root),
            "framework": "bmad",
            "profile": "feature",
        },
    )

    assert finalized.status_code == 422
    assert "provider" in finalized.text
    assert "model" in finalized.text


def test_materializer_denies_network_permissions_and_retries(
    app_client: TestClient, test_settings: Settings, monkeypatch: Any
) -> None:
    _create_work(app_client)
    repo_root = test_settings.workspace_root / "repo"
    _mark_framework_ready(repo_root, "bmad")
    planning_dir = repo_root / ".atelier" / "planning" / "WRK-001"
    fake_supervisor = _FakeMaterializerSupervisor()

    @asynccontextmanager
    async def fake_connect(*args: Any, **_kwargs: Any) -> Any:
        req = args[-1]
        assert isinstance(req, chat_runtime.ConnectChatRuntimeRequest)
        yield _FakeMaterializerSubscription(
            app_client.app.state.chatstore,
            req.chat_slug,
            planning_dir,
        )

    monkeypatch.setattr(materialize.chat_runtime, "connect_chat", fake_connect)

    view = asyncio.run(
        materialize.execute(
            app_client.app.state.workstore,
            app_client.app.state.chatstore,
            app_client.app.state.projectstore,
            app_client.app.state.planningfiles,
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
    assert fake_supervisor.permissions == [("mat-perm-1", "deny")]
    assert len(fake_supervisor.inputs) == 1
    assert "atelier_plan_materialization" in fake_supervisor.inputs[0]


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
    planning_dir = test_settings.workspace_root / "repo" / ".atelier" / "planning" / "WRK-001"
    (planning_dir / "design-guide.md").unlink()
    (planning_dir / "design-guidance.md").write_text("# Legacy Design Guidance\n")

    res = app_client.get("/api/works/WRK-001/plan")

    assert res.status_code == 200, res.text
    architecture = next(a for a in res.json()["artifacts"] if a["id"] == "architecture")
    assert architecture["path"] == "design-guidance.md"


def test_get_plan_returns_404_before_planning_starts(app_client: TestClient) -> None:
    _create_work(app_client)

    res = app_client.get("/api/works/WRK-001/plan")

    assert res.status_code == 404


def test_update_artifact_persists_markdown(
    app_client: TestClient, test_settings: Settings
) -> None:
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
    assert "Approve the latest source changes" not in " ".join(
        body["artifact"]["launch_blockers"]
    )
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
    planning_dir = repo_root / ".atelier" / "planning" / "WRK-001"
    stories = planning_dir / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    (planning_dir / "intent.md").write_text("# Intent\n")
    (planning_dir / "design-guide.md").write_text("# Design Guide\n")
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
    assert "Dependency architecture is not complete or ready for review." in story[
        "launch_blockers"
    ]

    approve = app_client.post("/api/works/WRK-001/plan/approve")
    assert approve.status_code == 200, approve.text
    approved = app_client.get("/api/works/WRK-001/plan").json()
    story = next(a for a in approved["artifacts"] if a["id"] == "story-001")
    assert story["launchable"] is True
    assert story["launch_blockers"] == []


def test_accept_story_writes_summary(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")

    res = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/accept",
        json={"summary": "Planner MVP accepted."},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["artifact"]["status"] == "accepted"
    summary = _planning_file(test_settings, "summaries/story-001.md")
    assert "Planner MVP accepted." in summary.read_text()


def test_artifact_run_report_and_acceptance_are_recorded(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200

    run = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"agent_slug": "AGT-001"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["artifact"]["runs"][0]["status"] == "running"

    report = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/report",
        json={
            **_complete_report("Implemented the source-backed planning path."),
            "agent_slug": "AGT-001",
        },
    )
    assert report.status_code == 200, report.text
    run_state = report.json()["artifact"]["runs"][0]
    assert run_state["status"] == "completed_pending_review"
    assert run_state["loop_status"] == "completed"
    assert run_state["loop_status_reason"] == "Report is complete and ready for review."

    accepted = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/accept",
        json={
            "agent_slug": "AGT-001",
            "summary": "Accepted after review.",
            "divergences": "None",
            "validation_evidence": "pytest passed",
        },
    )

    assert accepted.status_code == 200, accepted.text
    run_state = accepted.json()["artifact"]["runs"][0]
    assert run_state["status"] == "accepted"
    assert run_state["report_path"].endswith("summaries/story-001.md")
    summary = _planning_file(test_settings, "summaries/story-001.md").read_text()
    assert "## Divergences" in summary
    assert "pytest passed" in summary


def test_incomplete_artifact_report_needs_agent(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    assert app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"agent_slug": "AGT-001"},
    ).status_code == 200

    report = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/report",
        json={"agent_slug": "AGT-001", "summary": "Started the work."},
    )

    assert report.status_code == 200, report.text
    run_state = report.json()["artifact"]["runs"][0]
    assert run_state["status"] == "needs_attention"
    assert run_state["loop_status"] == "needs_agent"
    assert run_state["loop_status_reason"] == (
        "Report is incomplete; the agent needs to continue."
    )
    assert "Missing required report field: divergences." in run_state[
        "loop_latest_assessment"
    ]


def test_blocker_artifact_report_blocks_for_user(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")

    report = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/report",
        json={
            **_complete_report("Could not finish."),
            "blockers": "Need the user to choose an API contract.",
        },
    )

    assert report.status_code == 200, report.text
    run_state = report.json()["artifact"]["runs"][0]
    assert run_state["status"] == "blocked"
    assert run_state["loop_status"] == "blocked_user"
    assert run_state["loop_status_reason"] == (
        "The agent reported a blocker that needs user input."
    )
    assert run_state["loop_latest_assessment"] == [
        "Blocker reported: Need the user to choose an API contract."
    ]


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
    assert story["launch_blockers"] == [
        "Dependency story-001 is not complete or ready for review."
    ]

    report = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/report",
        json=_complete_report("Ready for dependent work."),
    )
    assert report.status_code == 200, report.text
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

    report = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/report",
        json=_complete_report("Ready for review."),
    )
    assert report.status_code == 200, report.text
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


def test_ingest_report_reads_latest_agent_transcript_and_marks_review(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    agent = _create_agent(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    run = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"agent_slug": agent["slug"]},
    )
    assert run.status_code == 200, run.text
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent["slug"],
        {
            "type": "message_complete",
            "ts": "2026-06-29T00:00:00+00:00",
            "text": """## Summary

Implemented the artifact.

## Divergences

None.

## Skipped Scope

None.

## Blockers

None.

## Decisions

Used the existing plan.

## Changes

Updated the implementation.

## Validation Evidence

pytest passed

## Proposed Source

```markdown
# Story 001: Plan Planner

## Scope

Implemented from the report.

## Acceptance Criteria

- Report proposal is reviewable.

## Dependencies

- None
```
""",
        },
    )

    ingested = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{agent['slug']}/ingest-report"
    )

    assert ingested.status_code == 200, ingested.text
    run_state = ingested.json()["artifact"]["runs"][0]
    assert run_state["status"] == "completed_pending_review"
    assert run_state["loop_status"] == "completed"
    assert run_state["summary"] == "Implemented the artifact."
    assert run_state["validation_evidence"] == "pytest passed"
    proposal = ingested.json()["artifact"]["proposals"][0]
    assert proposal["status"] == "pending"
    assert "Implemented from the report." in proposal["proposed_content"]


def test_artifact_run_cleanup_is_recorded(
    app_client: TestClient, test_settings: Settings
) -> None:
    _create_work(app_client)
    _start_plan(app_client, test_settings.workspace_root / "repo")
    agent = _create_agent(app_client, test_settings.workspace_root / "repo")
    assert app_client.post("/api/works/WRK-001/plan/approve").status_code == 200
    assert app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/runs",
        json={"agent_slug": agent["slug"]},
    ).status_code == 200
    accepted = app_client.post(
        "/api/works/WRK-001/plan/artifacts/story-001/accept",
        json={"agent_slug": agent["slug"], "summary": "Done."},
    )
    assert accepted.status_code == 200, accepted.text

    cleaned = app_client.post(
        f"/api/works/WRK-001/plan/artifacts/story-001/runs/{agent['slug']}/cleanup"
    )

    assert cleaned.status_code == 200, cleaned.text
    assert cleaned.json()["artifact"]["runs"][0]["cleanup_at"] is not None


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


def _create_materializer_chat(client: TestClient, root: Path) -> str:
    record = client.app.state.chatstore.create_chat(
        CreateChatRequest(
            provider="codex",
            model="gpt-5.4",
            first_message="Materialize an Atelier source-backed plan.",
            title="Planning materializer",
            grounding=ChatGrounding(kind="work", ref="WRK-001"),
            working_directory=str(root),
            options={"sandbox": "workspace-write", "approval_mode": "never"},
        )
    )
    assert record.chat.slug is not None
    return record.chat.slug


class _FakeMaterializerSupervisor:
    """Capture materializer permissions and follow-up prompts."""

    def __init__(self) -> None:
        self.permissions: list[tuple[str, str]] = []
        self.inputs: list[str] = []

    async def resolve_permission(
        self, _chat_slug: str, request_id: str, decision: str
    ) -> None:
        self.permissions.append((request_id, decision))

    async def send_input(
        self, _chat_slug: str, text: str, *, record_user_input: bool = True
    ) -> None:
        self.inputs.append(text)


class _FakeMaterializerSubscription:
    """Emit a permission prompt, an incomplete turn, then a final report."""

    def __init__(self, chatstore: Any, chat_slug: str, planning_dir: Path) -> None:
        self._chatstore = chatstore
        self._chat_slug = chat_slug
        self._planning_dir = planning_dir

    def stream(self) -> Any:
        return self._events()

    async def _events(self) -> Any:
        yield {
            "type": "permission_request",
            "request_id": "mat-perm-1",
            "tool_name": "Bash",
            "tool_input": {"command": "curl https://example.com/template.md"},
        }
        yield {"type": "status_change", "status": "idle"}
        self._write_reported_plan()
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
        self._chatstore.append_transcript_event_with_seq(
            self._chat_slug,
            {
                "type": "message_complete",
                "ts": "2026-06-29T00:00:00+00:00",
                "text": json.dumps(report),
            },
        )
        yield {"type": "message_complete", "text": json.dumps(report)}

    def _write_reported_plan(self) -> None:
        stories = self._planning_dir / "stories"
        stories.mkdir(parents=True, exist_ok=True)
        (stories / "story-001.md").write_text(
            "# Story 001\n\n## Acceptance Criteria\n\n- Plan exists.\n"
        )


def _planning_file(settings: Settings, rel_path: str) -> Path:
    return (
        settings.workspace_root
        / "repo"
        / ".atelier"
        / "planning"
        / "WRK-001"
        / rel_path
    )
