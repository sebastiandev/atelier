"""Integration coverage for standalone objective loop runs."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.domain.agents import AmpAgentConfig
from src.domain.loop import monitor as loop_monitor
from src.domain.loop.dtos import LoopFailureKind
from src.domain.loop.store import LoopRunStore, loop_run_key
from src.domain.supervisor import service as supervisor_service
from src.domain.workstore.dtos import RecordArtifactRequest
from src.infrastructure.agents import StubAgentAdapter
from src.infrastructure.agents.factory import build_adapter
from src.settings import Settings

_REAL_DATETIME = datetime


@pytest.fixture
def quiet_amp_dispatch() -> Iterator[None]:
    """Keep the test agent live without emitting provider events."""
    original = build_adapter.dispatch(AmpAgentConfig)

    def _quiet(_config: AmpAgentConfig, _settings: Settings) -> StubAgentAdapter:
        """Return a provider runtime that remains live and silent."""
        return StubAgentAdapter([], keep_alive=True)

    build_adapter.register(AmpAgentConfig)(_quiet)
    try:
        yield
    finally:
        build_adapter.register(AmpAgentConfig)(original)


def _advance_monitor_clock(monkeypatch: pytest.MonkeyPatch, *, minutes: int) -> type[datetime]:
    """Move monitor wall time forward without sleeping in integration tests."""

    class AdvancedDateTime(_REAL_DATETIME):
        """Datetime replacement with a fixed relative offset."""

        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            """Return real wall time plus the requested offset."""
            return _REAL_DATETIME.now(tz) + timedelta(minutes=minutes)

    monkeypatch.setattr(loop_monitor, "datetime", AdvancedDateTime)
    return AdvancedDateTime


def test_objective_loop_run_starts_completes_and_accepts(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    created_work = app_client.post(
        "/api/works",
        json={"name": "Loop work", "description": "Fix the launch path."},
    )
    assert created_work.status_code == 201, created_work.text
    root = tmp_path / "repository"
    root.mkdir()
    definition = app_client.get("/api/loops/atelier-fast").json()

    started = app_client.post(
        "/api/works/WRK-001/runs",
        json={
            "goal": "Implement the standalone loop endpoint.",
            "root_path": str(root),
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
            "provider": "amp",
            "model": "smart",
            "options": {},
        },
    )

    assert started.status_code == 201, started.text
    run = started.json()
    assert run["id"] == "run-001"
    assert run["status"] == "running"
    assert run["current_stage_id"] == "implementation"
    assert run["loop_definition"] == {
        key: definition[key] for key in ("id", "name", "description", "scope", "revision", "stages")
    }
    assert app_client.get("/api/works/WRK-001/runs").json()[0]["id"] == "run-001"
    fetched = app_client.get("/api/works/WRK-001/runs/run-001")
    assert fetched.status_code == 200
    assert fetched.json()["loop_definition"] == run["loop_definition"]

    agent_slug = run["stages"][0]["agent_slug"]
    _wait_for_agent_idle(app_client, agent_slug)
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "message_complete",
            "ts": "2026-07-13T00:00:00+00:00",
            "text": json.dumps(
                {
                    "atelier_loop_step_report": {
                        "outcome": "pass",
                        "summary": "Implemented the endpoint.",
                        "findings": [],
                        "changes": "Added the standalone run route.",
                        "validation_evidence": "integration test passed",
                        "divergences": "None.",
                        "skipped_scope": "None.",
                        "blocker": "None.",
                        "artifact_refs": [],
                    }
                }
            ),
        },
    )
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {"type": "status_change", "status": "idle"},
    )

    completed = _wait_for_status(app_client, "awaiting_approval")
    assert completed["summary"] == "Implemented the endpoint."
    assert completed["stages"][0]["status"] == "passed"
    assert completed["stages"][0]["reports"][0]["summary"] == "Implemented the endpoint."

    accepted = app_client.post("/api/works/WRK-001/runs/run-001/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["accepted_at"] is not None
    for _ in range(100):
        if not app_client.app.state.supervisor.is_registered(agent_slug):
            break
        time.sleep(0.01)
    assert not app_client.app.state.supervisor.is_registered(agent_slug)
    assert any(
        agent["slug"] == agent_slug for agent in app_client.get("/api/works/WRK-001/agents").json()
    )
    assert app_client.app.state.workstore.read_transcript_from_cursor("WRK-001", agent_slug, 0)
    assert app_client.get("/api/works/WRK-001").json()["mode"] == "loop"


def test_completed_work_must_be_reopened_before_starting_loop(
    app_client: TestClient, tmp_path: Path
) -> None:
    created = app_client.post("/api/works", json={"name": "Loop work", "description": "A task."})
    assert created.status_code == 201
    assert app_client.post("/api/works/WRK-001/complete").status_code == 200
    root = tmp_path / "repository"
    root.mkdir()
    definition = app_client.get("/api/loops/atelier-fast").json()
    payload = {
        "goal": "Implement the task.",
        "root_path": str(root),
        "loop_definition_id": definition["id"],
        "loop_revision": definition["revision"],
        "provider": "amp",
        "model": "smart",
        "options": {},
    }

    blocked = app_client.post("/api/works/WRK-001/runs", json=payload)

    assert blocked.status_code == 409
    assert "reopen" in blocked.json()["detail"]
    assert app_client.patch("/api/works/WRK-001", json={"status": "active"}).status_code == 200
    assert app_client.post("/api/works/WRK-001/runs", json=payload).status_code == 201


def test_work_with_active_loop_cannot_be_completed(app_client: TestClient, tmp_path: Path) -> None:
    _start_run(app_client, tmp_path)

    response = app_client.post("/api/works/WRK-001/complete")

    assert response.status_code == 409
    assert "active run" in response.json()["detail"]
    assert app_client.get("/api/works/WRK-001").json()["status"] == "active"


def test_work_loop_brief_round_trips(
    app_client: TestClient,
) -> None:
    created = app_client.post(
        "/api/works",
        json={"name": "Briefed work", "description": "Keep task input on the work."},
    )
    assert created.status_code == 201, created.text
    assert app_client.get("/api/works/WRK-001/loop-brief").json() is None
    brief = {
        "goal": "Implement the brief contract.",
        "stages": [
            {
                "stage_id": "implementation",
                "note": "Keep the public API backward compatible.",
                "context": [
                    {"kind": "file", "value": "docs/api-flows.md"},
                    {"kind": "note", "value": "Avoid a schema migration."},
                ],
                "agent": {
                    "provider": "amp",
                    "model": "smart",
                    "options": {},
                },
                "approved_command_prefixes": ["dt sh -s app-endpoints"],
            }
        ],
    }

    saved = app_client.put("/api/works/WRK-001/loop-brief", json=brief)

    assert saved.status_code == 200, saved.text
    assert saved.json() == brief
    fetched = app_client.get("/api/works/WRK-001/loop-brief")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == brief


def _reviewed_with_required_note(app_client: TestClient) -> dict:
    """A WRK-001 fork of Atelier Reviewed whose review stage demands a note.

    The built-ins no longer declare ``note_required`` -- a reviewer already
    gets the goal, the target and the diff -- so the contract is exercised
    through a definition that opts into it, which is who it is for.
    """
    payload = app_client.get("/api/loops/atelier-reviewed").json()
    review = next(stage for stage in payload["stages"] if stage["id"] == "code-review")
    review["note_required"] = True
    saved = app_client.post(
        "/api/loops",
        json={**payload, "scope": "work", "work_slug": "WRK-001", "expected_revision": None},
    )
    assert saved.status_code == 201, saved.text
    return saved.json()


def test_explicit_brief_must_fill_required_review_note(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    created = app_client.post(
        "/api/works",
        json={"name": "Reviewed work", "description": "Require review direction."},
    )
    assert created.status_code == 201, created.text
    root = tmp_path / "repository"
    root.mkdir()
    definition = _reviewed_with_required_note(app_client)

    response = app_client.post(
        "/api/works/WRK-001/runs",
        json={
            "goal": "Implement and review the change.",
            "root_path": str(root),
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
            "provider": "amp",
            "model": "smart",
            "options": {},
            "brief": {
                "goal": "Implement and review the change.",
                "stages": [{"stage_id": "code-review", "note": "", "context": []}],
            },
        },
    )

    assert response.status_code == 422, response.text
    assert "Code review" in response.json()["detail"]
    assert app_client.get("/api/works/WRK-001/agents").json() == []


def test_verify_follow_up_requires_a_review_or_check_stage(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    accepted = _complete_and_accept(app_client, _start_run(app_client, tmp_path))
    assert accepted["status"] == "accepted"

    response = app_client.post(
        "/api/works/WRK-001/runs/run-001/rerun",
        json={"kind": "verify"},
    )

    assert response.status_code == 422, response.text
    assert len(app_client.get("/api/works/WRK-001/runs").json()) == 1


def test_verify_follow_up_starts_at_review_and_skips_implementation(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    created = app_client.post(
        "/api/works",
        json={"name": "Reviewed work", "description": "Review the current state."},
    )
    assert created.status_code == 201, created.text
    root = tmp_path / "repository"
    root.mkdir()
    definition = app_client.get("/api/loops/atelier-reviewed").json()
    goal = "Implement and review the change."
    started = app_client.post(
        "/api/works/WRK-001/runs",
        json={
            "goal": goal,
            "root_path": str(root),
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
            "provider": "amp",
            "model": "smart",
            "options": {},
        },
    ).json()

    def pass_stage(agent_slug: str, summary: str) -> None:
        """Append one passing report to the active Reviewed stage."""
        _wait_for_agent_idle(app_client, agent_slug)
        app_client.app.state.workstore.append_transcript_event_with_seq(
            "WRK-001",
            agent_slug,
            {
                "type": "message_complete",
                "text": json.dumps(
                    {
                        "atelier_loop_step_report": {
                            "outcome": "pass",
                            "summary": summary,
                            "findings": [],
                            "changes": "Updated the code.",
                            "validation_evidence": "tests passed",
                            "divergences": "None.",
                            "skipped_scope": "None.",
                            "blocker": "None.",
                            "artifact_refs": [],
                        }
                    }
                ),
            },
        )
        app_client.app.state.workstore.append_transcript_event_with_seq(
            "WRK-001",
            agent_slug,
            {"type": "status_change", "status": "idle"},
        )

    pass_stage(started["stages"][0]["agent_slug"], "Implemented the change.")
    reviewing = _wait_for_stage(app_client, "code-review")
    review = next(stage for stage in reviewing["stages"] if stage["id"] == "code-review")
    pass_stage(review["agent_slug"], "The change passes review.")
    _wait_for_status(app_client, "awaiting_approval")
    accepted = app_client.post("/api/works/WRK-001/runs/run-001/accept")
    assert accepted.status_code == 200, accepted.text

    response = app_client.post(
        "/api/works/WRK-001/runs/run-001/rerun",
        json={"kind": "verify"},
    )

    assert response.status_code == 201, response.text
    rerun = response.json()
    assert rerun["id"] == "run-002"
    assert rerun["run_kind"] == "verify"
    assert rerun["seed_label"] == "manual edits"
    assert rerun["current_stage_id"] == "code-review"
    assert [stage["status"] for stage in rerun["stages"]] == [
        "skipped",
        "running",
        "pending",
    ]


def test_amend_follow_up_pins_feedback_in_the_same_workspace(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    accepted = _complete_and_accept(app_client, _start_run(app_client, tmp_path))
    feedback = "Handle the empty-input case before approval."

    response = app_client.post(
        "/api/works/WRK-001/runs/run-001/rerun",
        json={"kind": "amend", "note": feedback},
    )

    assert response.status_code == 201, response.text
    rerun = response.json()
    assert rerun["id"] == "run-002"
    assert rerun["run_kind"] == "amend"
    assert rerun["seed_label"] == "feedback"
    assert rerun["source_run_id"] == "run-001"
    assert rerun["workspace_path"] == accepted["workspace_path"]
    assert rerun["current_stage_id"] == "implementation"
    assert next(
        stage["note"] for stage in rerun["brief"]["stages"] if stage["stage_id"] == "implementation"
    ).endswith(feedback)


def test_rerun_reuses_the_accepted_workspace(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    first = _start_run(app_client, tmp_path, git=True)
    accepted = _complete_and_accept(app_client, first)
    source_workspace = Path(str(accepted["workspace_path"]))
    (source_workspace / "carried.txt").write_text("accepted state")

    response = app_client.post("/api/works/WRK-001/runs/run-001/rerun")

    assert response.status_code == 201, response.text
    rerun = response.json()
    assert rerun["source_run_id"] == "run-001"
    assert rerun["workspace_path"] == str(source_workspace)
    assert (Path(rerun["workspace_path"]) / "carried.txt").read_text() == "accepted state"


def test_rerun_reuses_a_failed_workspace(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    first = _start_run(app_client, tmp_path)
    stages = first["stages"]
    assert isinstance(stages, list)
    agent_slug = stages[0]["agent_slug"]
    assert isinstance(agent_slug, str)
    _wait_for_agent_idle(app_client, agent_slug)
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {"type": "error", "message": "Provider stopped."},
    )
    failed = _wait_for_status(app_client, "failed")
    source_workspace = Path(str(failed["workspace_path"]))
    (source_workspace / "carried.txt").write_text("failed run state")

    response = app_client.post("/api/works/WRK-001/runs/run-001/rerun")

    assert response.status_code == 201, response.text
    rerun = response.json()
    assert rerun["source_run_id"] == "run-001"
    assert rerun["workspace_path"] == str(source_workspace)
    assert (Path(rerun["workspace_path"]) / "carried.txt").read_text() == "failed run state"


def test_edited_run_reuses_the_exact_legacy_workspace(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    first = _start_run(app_client, tmp_path, git=True)
    first_agent_slug = _first_agent_slug(first)
    _wait_for_agent_idle(app_client, first_agent_slug)
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        first_agent_slug,
        {"type": "error", "message": "Provider stopped."},
    )
    _wait_for_status(app_client, "failed")

    root = tmp_path / "repository"
    legacy_response = app_client.post(
        "/api/works/WRK-001/agents",
        json={
            "name": "Legacy loop workspace",
            "persona": "developer",
            "role": "Continue the failed run.",
            "provider": "amp",
            "model": "smart",
            "folder": str(root),
        },
    )
    assert legacy_response.status_code == 201, legacy_response.text
    legacy_agent = legacy_response.json()
    legacy_slug = legacy_agent["slug"]
    legacy_workspace = Path(legacy_agent["worktree_path"])
    (legacy_workspace / "README.md").write_text("retained tracked edit\n")
    (legacy_workspace / "carried.txt").write_text("retained untracked edit\n")

    store = LoopRunStore(app_client.app.state.loop_runs)
    target = store.load("WRK-001", "run-001")
    assert target is not None
    target.run["workspace_path"] = str(legacy_workspace)
    target.run["agent_slug"] = legacy_slug
    loop = target.run["loop"]
    assert isinstance(loop, dict)
    loop["source_agent_slug"] = legacy_slug
    loop["owned_agent_slugs"] = [legacy_slug]
    stages = loop["stages"]
    assert isinstance(stages, list) and isinstance(stages[0], dict)
    stages[0]["agent_slug"] = legacy_slug
    store.save(target)

    app_client.portal.call(app_client.app.state.supervisor.stop_agent, first_agent_slug)
    app_client.app.state.worktree_manager.remove("WRK-001", "loop")
    app_client.app.state.workstore.delete_agent(first_agent_slug)

    base = app_client.get("/api/loops/atelier-fast").json()
    created_definition = app_client.post(
        "/api/loops",
        json={
            "id": "edited-loop",
            "name": "Edited loop",
            "description": "Before editing.",
            "scope": "work",
            "work_slug": "WRK-001",
            "stages": base["stages"],
        },
    )
    assert created_definition.status_code == 201, created_definition.text
    original = created_definition.json()
    source_target = store.load("WRK-001", "run-001")
    assert source_target is not None
    source_loop = source_target.run["loop"]
    assert isinstance(source_loop, dict)
    source_loop["definition_id"] = original["id"]
    source_loop["definition_name"] = original["name"]
    source_loop["definition_revision"] = original["revision"]
    source_loop["definition_snapshot"] = {
        key: original[key] for key in ("id", "name", "description", "scope", "revision", "stages")
    }
    store.save(source_target)
    updated_definition = app_client.put(
        "/api/loops/edited-loop",
        json={
            "id": "edited-loop",
            "name": "Edited loop",
            "description": "Use this updated revision.",
            "scope": "work",
            "work_slug": "WRK-001",
            "expected_revision": original["revision"],
            "stages": original["stages"],
        },
    )
    assert updated_definition.status_code == 200, updated_definition.text
    updated = updated_definition.json()

    response = app_client.post(
        "/api/works/WRK-001/runs",
        json={
            "goal": first["goal"],
            "root_path": str(root),
            "loop_definition_id": updated["id"],
            "loop_revision": updated["revision"],
            "provider": "amp",
            "model": "smart",
            "options": {},
            "source_run_id": "run-001",
        },
    )

    assert response.status_code == 201, response.text
    rerun = response.json()
    assert rerun["source_run_id"] == "run-001"
    assert rerun["loop_definition_id"] == "edited-loop"
    assert rerun["loop_definition_revision"] == updated["revision"]
    assert rerun["workspace_path"] == str(legacy_workspace)
    assert (legacy_workspace / "README.md").read_text() == "retained tracked edit\n"
    assert (legacy_workspace / "carried.txt").read_text() == "retained untracked edit\n"


def test_legacy_loop_workspace_reads_as_the_retained_source_workspace(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    _start_run(app_client, tmp_path)
    retained_workspace = tmp_path / "worktrees" / "agt-33"
    store = LoopRunStore(app_client.app.state.loop_runs)
    source = store.load("WRK-001", "run-001")
    assert source is not None
    source.run["workspace_path"] = str(retained_workspace)
    store.save(source)

    legacy = replace(source, run_id="run-002", run=deepcopy(source.run))
    legacy.run["id"] = "run-002"
    legacy.run["number"] = 2
    legacy.run["source_run_id"] = "run-001"
    legacy.run["workspace_path"] = str(tmp_path / "worktrees" / "loop")
    loop = legacy.run["loop"]
    assert isinstance(loop, dict)
    loop["loop_run_id"] = loop_run_key("run-002")
    store.save(legacy)

    listed = app_client.get("/api/works/WRK-001/runs")
    fetched = app_client.get("/api/works/WRK-001/runs/run-002")

    assert listed.status_code == 200, listed.text
    assert fetched.status_code == 200, fetched.text
    listed_by_id = {item["id"]: item for item in listed.json()}
    assert listed_by_id["run-002"]["workspace_path"] == str(retained_workspace)
    assert fetched.json()["workspace_path"] == str(retained_workspace)


def test_review_stage_reuses_the_objective_workspace(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    created = app_client.post(
        "/api/works",
        json={"name": "Reviewed work", "description": "Review the change."},
    )
    assert created.status_code == 201, created.text
    root = tmp_path / "repository"
    root.mkdir()
    definition = app_client.get("/api/loops/atelier-reviewed").json()
    started = app_client.post(
        "/api/works/WRK-001/runs",
        json={
            "goal": "Implement and review the change.",
            "root_path": str(root),
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
            "provider": "amp",
            "model": "smart",
            "options": {},
        },
    ).json()
    implementation_slug = started["stages"][0]["agent_slug"]
    _wait_for_agent_idle(app_client, implementation_slug)
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        implementation_slug,
        {
            "type": "message_complete",
            "text": json.dumps(
                {
                    "atelier_loop_step_report": {
                        "outcome": "pass",
                        "summary": "Implemented the change.",
                        "findings": [],
                        "changes": "Updated the code.",
                        "validation_evidence": "tests passed",
                        "divergences": "None.",
                        "skipped_scope": "None.",
                        "blocker": "None.",
                        "artifact_refs": [],
                    }
                }
            ),
        },
    )
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        implementation_slug,
        {"type": "status_change", "status": "idle"},
    )

    _wait_for_stage(app_client, "code-review")
    agents = app_client.get("/api/works/WRK-001/agents").json()
    assert len(agents) == 2
    assert {agent["worktree_path"] for agent in agents} == {str(root)}


def test_cancelled_run_cleanup_preserves_history_and_workspace(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    started = _start_run(app_client, tmp_path)
    workspace = Path(str(started["workspace_path"]))

    cancelled = app_client.post("/api/works/WRK-001/runs/run-001/cancel")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_at"] is not None

    cleaned = app_client.post("/api/works/WRK-001/runs/run-001/cleanup")
    assert cleaned.status_code == 200, cleaned.text
    assert cleaned.json()["status"] == "cleaned"
    assert cleaned.json()["cleanup_at"] is not None
    assert cleaned.json()["accepted_at"] is None
    agents = app_client.get("/api/works/WRK-001/agents").json()
    assert agents
    assert workspace.exists()
    assert app_client.app.state.workstore.read_transcript_from_cursor(
        "WRK-001", agents[0]["slug"], 0
    )


def test_agent_runtime_error_fails_without_report_retries(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    started = _start_run(app_client, tmp_path)
    stages = started["stages"]
    assert isinstance(stages, list)
    agent_slug = stages[0]["agent_slug"]
    assert isinstance(agent_slug, str)
    _wait_for_agent_idle(app_client, agent_slug)
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "error",
            "ts": "2026-07-13T00:00:00+00:00",
            "message": "Codex ACP crashed.",
        },
    )

    failed = _wait_for_status(app_client, "failed")

    assert failed["status_reason"] == "Agent runtime failed: Codex ACP crashed."
    assert failed["stages"][0]["status"] == "failed"
    assert failed["stages"][0]["attempt"] == 1
    for _ in range(100):
        if not app_client.app.state.supervisor.is_registered(agent_slug):
            break
        time.sleep(0.01)
    assert not app_client.app.state.supervisor.is_registered(agent_slug)
    assert any(
        agent["slug"] == agent_slug for agent in app_client.get("/api/works/WRK-001/agents").json()
    )
    target = LoopRunStore(app_client.app.state.loop_runs).load("WRK-001", "run-001")
    assert target is not None
    assert target.run["loop"]["failure_kind"] == LoopFailureKind.PROVIDER_RUNTIME.value


def test_connection_closed_continues_same_stage_once(
    app_client: TestClient,
    tmp_path: Path,
    quiet_amp_dispatch: None,
) -> None:
    started = _start_run(app_client, tmp_path)
    agent_slug = _first_agent_slug(started)
    portal = app_client.portal
    assert portal is not None
    portal.call(app_client.app.state.supervisor.stop_agent, agent_slug)
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "error",
            "ts": "2026-07-15T00:00:00+00:00",
            "message": "Connection closed",
        },
    )

    deadline = time.monotonic() + 3
    user_inputs: list[dict[str, object]] = []
    current = started
    while time.monotonic() < deadline:
        user_inputs = [
            event
            for event in app_client.app.state.workstore.read_transcript_from_cursor(
                "WRK-001", agent_slug, 0
            )
            if event.get("type") == "user_input"
        ]
        current = app_client.get("/api/works/WRK-001/runs/run-001").json()
        if len(user_inputs) >= 2 and "continued" in current["status_reason"]:
            break
        time.sleep(0.05)

    assert len(user_inputs) == 2
    assert str(user_inputs[-1]["text"]).startswith("Continue stage `implementation`")
    assert current["status"] == "running"
    assert current["stages"][0]["agent_slug"] == agent_slug
    assert current["stages"][0]["attempt"] == 1

    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "error",
            "ts": "2026-07-15T00:01:00+00:00",
            "message": "Connection closed",
        },
    )
    failed = _wait_for_status(app_client, "failed")

    assert failed["status_reason"] == "Agent runtime failed: Connection closed"
    assert failed["stages"][0]["attempt"] == 1


def test_silent_live_stage_restarts_once_with_original_request(
    app_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quiet_amp_dispatch: None,
) -> None:
    started = _start_run(app_client, tmp_path)
    agent_slug = _first_agent_slug(started)
    advanced = _advance_monitor_clock(monkeypatch, minutes=6)
    monkeypatch.setattr(supervisor_service, "datetime", advanced)

    deadline = time.monotonic() + 3
    user_inputs: list[dict[str, object]] = []
    current = started
    while time.monotonic() < deadline:
        user_inputs = [
            event
            for event in app_client.app.state.workstore.read_transcript_from_cursor(
                "WRK-001", agent_slug, 0
            )
            if event.get("type") == "user_input"
        ]
        current = app_client.get("/api/works/WRK-001/runs/run-001").json()
        if len(user_inputs) >= 2 and "reconnected" in current["status_reason"]:
            break
        time.sleep(0.05)

    assert len(user_inputs) == 2
    recovery = str(user_inputs[-1]["text"])
    assert recovery.startswith("Continue stage `implementation`")
    assert "Original stage request:" in recovery
    assert "Implement the standalone loop endpoint." in recovery
    assert current["stages"][0]["attempt"] == 1
    assert app_client.app.state.supervisor.is_registered(agent_slug)
    time.sleep(0.1)
    assert (
        len(
            [
                event
                for event in app_client.app.state.workstore.read_transcript_from_cursor(
                    "WRK-001", agent_slug, 0
                )
                if event.get("type") == "user_input"
            ]
        )
        == 2
    )


def test_stale_permission_denial_continues_active_stage_once(
    app_client: TestClient,
    tmp_path: Path,
    quiet_amp_dispatch: None,
) -> None:
    started = _start_run(app_client, tmp_path)
    agent_slug = _first_agent_slug(started)
    transcript = app_client.app.state.workstore
    transcript.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "permission_request",
            "request_id": "expired-request",
            "tool_name": "tool",
        },
    )
    transcript.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "permission_decision",
            "request_id": "expired-request",
            "decision": "deny",
            "stale": True,
        },
    )

    deadline = time.monotonic() + 3
    recovery_inputs: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        recovery_inputs = [
            event
            for event in transcript.read_transcript_from_cursor("WRK-001", agent_slug, 0)
            if event.get("type") == "user_input"
            and str(event.get("text", "")).startswith("Continue stage `implementation`")
        ]
        if recovery_inputs:
            break
        time.sleep(0.02)

    assert len(recovery_inputs) == 1
    time.sleep(0.1)
    all_inputs = [
        event
        for event in transcript.read_transcript_from_cursor("WRK-001", agent_slug, 0)
        if event.get("type") == "user_input"
        and str(event.get("text", "")).startswith("Continue stage `implementation`")
    ]
    assert len(all_inputs) == 1


def test_silent_missing_runtime_is_reconnected_once(
    app_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quiet_amp_dispatch: None,
) -> None:
    started = _start_run(app_client, tmp_path)
    agent_slug = _first_agent_slug(started)
    portal = app_client.portal
    assert portal is not None
    portal.call(app_client.app.state.supervisor.stop_agent, agent_slug)
    advanced = _advance_monitor_clock(monkeypatch, minutes=6)
    monkeypatch.setattr(supervisor_service, "datetime", advanced)

    deadline = time.monotonic() + 3
    user_inputs: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        user_inputs = [
            event
            for event in app_client.app.state.workstore.read_transcript_from_cursor(
                "WRK-001", agent_slug, 0
            )
            if event.get("type") == "user_input"
        ]
        if len(user_inputs) >= 2:
            break
        time.sleep(0.05)

    assert len(user_inputs) == 2
    assert str(user_inputs[-1]["text"]).startswith("Continue stage `implementation`")
    assert app_client.app.state.supervisor.is_registered(agent_slug)


def test_agent_stage_timeout_fails_and_stops_runtime(
    app_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quiet_amp_dispatch: None,
) -> None:
    _advance_monitor_clock(monkeypatch, minutes=46)

    started = _start_run(app_client, tmp_path)
    failed = _wait_for_status(app_client, "failed")

    agent_slug = _first_agent_slug(started)
    assert failed["status_reason"] == (
        "Implementation timed out after 45 minutes without completing."
    )
    stages = failed["stages"]
    assert isinstance(stages, list)
    assert stages[0]["status"] == "failed"
    assert not app_client.app.state.supervisor.is_registered(agent_slug)


def test_failed_stage_retry_uses_new_agent_in_same_workspace(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    started = _start_run(app_client, tmp_path)
    stages = started["stages"]
    assert isinstance(stages, list)
    agent_slug = stages[0]["agent_slug"]
    assert isinstance(agent_slug, str)
    _wait_for_agent_idle(app_client, agent_slug)
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {"type": "error", "message": "Provider stopped."},
    )
    failed = _wait_for_status(app_client, "failed")
    store = LoopRunStore(app_client.app.state.loop_runs)
    target = store.load("WRK-001", "run-001")
    assert target is not None
    target.run["loop"]["failure_kind"] = LoopFailureKind.PROVIDER_RUNTIME.value
    target.run["loop"]["status_reason"] = "Provider diagnostic text may change."
    store.save(target)
    response = app_client.post("/api/works/WRK-001/runs/run-001/retry-stage")

    assert response.status_code == 200, response.text
    retried = response.json()
    assert retried["id"] == failed["id"] == "run-001"
    assert retried["status"] == "running"
    assert retried["workspace_path"] == failed["workspace_path"]
    retry_agent_slug = retried["stages"][0]["agent_slug"]
    assert retry_agent_slug != agent_slug
    assert retried["stages"][0]["attempt"] == 2
    assert len(app_client.get("/api/works/WRK-001/agents").json()) == 2
    agents = app_client.app.state.workstore.list_agents_for_work("WRK-001")
    original_agent = next(agent for agent in agents if agent.slug == agent_slug)
    retry_agent = next(agent for agent in agents if agent.slug == retry_agent_slug)
    assert retry_agent.worktree_slug == (original_agent.worktree_slug or agent_slug)
    inputs_after = [
        event
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", retry_agent_slug, 0
        )
        if event.get("type") == "user_input"
    ]
    assert len(inputs_after) == 1
    # The fresh retry agent has no memory of the failed attempt; hint that
    # the workspace may already carry its work so it continues rather than
    # silently redoing everything from scratch.
    assert "previous attempt" in inputs_after[0]["text"]
    assert "git status" in inputs_after[0]["text"]


def test_stage_retry_rejects_an_active_run(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    _start_run(app_client, tmp_path)

    response = app_client.post("/api/works/WRK-001/runs/run-001/retry-stage")

    assert response.status_code == 422, response.text


def _fail_run_for_retry(app_client: TestClient, tmp_path: Path) -> str:
    """Drive a run to a retryable failure; return the failed stage's agent."""
    started = _start_run(app_client, tmp_path)
    agent_slug = started["stages"][0]["agent_slug"]
    assert isinstance(agent_slug, str)
    _wait_for_agent_idle(app_client, agent_slug)
    app_client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001", agent_slug, {"type": "error", "message": "Provider stopped."}
    )
    _wait_for_status(app_client, "failed")
    store = LoopRunStore(app_client.app.state.loop_runs)
    target = store.load("WRK-001", "run-001")
    assert target is not None
    target.run["loop"]["failure_kind"] = LoopFailureKind.PROVIDER_RUNTIME.value
    store.save(target)
    return agent_slug


def test_stage_retry_applies_same_provider_model_override(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    original_slug = _fail_run_for_retry(app_client, tmp_path)

    # An override the provider rejects fails cleanly and leaves the run
    # retryable (amp has no reasoning-effort dial), so nothing was torn down.
    rejected = app_client.post(
        "/api/works/WRK-001/runs/run-001/retry-stage",
        json={"effort": "high"},
    )
    assert rejected.status_code == 422, rejected.text

    retried = app_client.post(
        "/api/works/WRK-001/runs/run-001/retry-stage",
        json={"model": "rush"},
    )
    assert retried.status_code == 200, retried.text
    retry_agent_slug = retried.json()["stages"][0]["agent_slug"]
    assert retry_agent_slug != original_slug
    agents = app_client.app.state.workstore.list_agents_for_work("WRK-001")
    retry_agent = next(agent for agent in agents if agent.slug == retry_agent_slug)
    assert retry_agent.model == "rush"  # overridden from the run's "smart"


def test_intermediate_agent_message_does_not_consume_report_retry(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    started = _start_run(app_client, tmp_path)
    stages = started["stages"]
    assert isinstance(stages, list)
    agent_slug = stages[0]["agent_slug"]
    assert isinstance(agent_slug, str)
    _wait_for_agent_idle(app_client, agent_slug)
    transcript = app_client.app.state.workstore
    transcript.append_transcript_event_with_seq(
        "WRK-001", agent_slug, {"type": "status_change", "status": "thinking"}
    )
    transcript.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "message_complete",
            "text": "I found the implementation path and am editing it now.",
        },
    )
    transcript.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {"type": "tool_call", "tool_id": "edit-1", "title": "Editing files"},
    )
    time.sleep(0.2)

    still_running = app_client.get("/api/works/WRK-001/runs/run-001").json()
    assert still_running["status"] == "running"
    assert still_running["stages"][0]["attempt"] == 1

    transcript.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "message_complete",
            "text": json.dumps(
                {
                    "atelier_loop_step_report": {
                        "outcome": "pass",
                        "summary": "Implemented the endpoint.",
                        "findings": [],
                        "changes": "Added the standalone run route.",
                        "validation_evidence": "integration test passed",
                        "divergences": "None.",
                        "skipped_scope": "None.",
                        "blocker": "None.",
                        "artifact_refs": [],
                    }
                }
            ),
        },
    )
    transcript.append_transcript_event_with_seq(
        "WRK-001", agent_slug, {"type": "status_change", "status": "idle"}
    )

    completed = _wait_for_status(app_client, "awaiting_approval")
    assert completed["stages"][0]["attempt"] == 1


def _send_back_from_a_human_review_gate(
    app_client: TestClient,
    tmp_path: Path,
) -> tuple[str, dict[str, Any]]:
    """Drive a gated run to a send-back; return the first agent and the run."""
    created = app_client.post(
        "/api/works",
        json={"name": "Gated work", "description": "Review before iterating."},
    )
    assert created.status_code == 201, created.text
    root = tmp_path / "repository"
    root.mkdir()
    definition = app_client.get("/api/loops/atelier-reviewed").json()
    started = app_client.post(
        "/api/works/WRK-001/runs",
        json={
            "goal": "Implement the gated change.",
            "root_path": str(root),
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
            "provider": "amp",
            "model": "smart",
            "options": {},
            "brief": {
                "goal": "Implement the gated change.",
                "stages": [
                    {
                        "stage_id": "code-review",
                        "note": "Check the public contract.",
                        "context": [],
                        "review_gate": "human_check",
                    }
                ],
            },
        },
    ).json()
    implementation_slug = started["stages"][0]["agent_slug"]
    _append_stage_report(app_client, implementation_slug, "pass", [])
    reviewing = _wait_for_stage(app_client, "code-review")
    review = next(stage for stage in reviewing["stages"] if stage["id"] == "code-review")
    _append_stage_report(
        app_client,
        review["agent_slug"],
        "changes_requested",
        ["Fix the race.", "Rename the fixture."],
    )

    blocked = _wait_for_status(app_client, "blocked_user")
    assert blocked["review_gate"]["mode"] == "human_check"
    assert blocked["review_gate"]["source"] == "run_override"
    assert blocked["review_gate"]["findings"] == ["Fix the race.", "Rename the fixture."]

    resumed = app_client.post(
        "/api/works/WRK-001/runs/run-001/resume",
        json={
            "gate_decision": "send_back",
            "enforced_findings": [0],
            "resolution_note": "Use the shared lock helper.",
        },
    )
    assert resumed.status_code == 200, resumed.text
    return implementation_slug, _wait_for_stage(app_client, "implementation")


def test_human_review_gate_pauses_and_sends_only_enforced_findings(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    implementation_slug, implementing = _send_back_from_a_human_review_gate(app_client, tmp_path)
    assert implementing["status"] == "running"
    assert implementing["pass_number"] == 2
    assert implementing["waived_findings_count"] == 1
    next_implementation = next(
        stage for stage in implementing["stages"] if stage["id"] == "implementation"
    )
    next_implementation_slug = next_implementation["agent_slug"]
    assert next_implementation_slug != implementation_slug
    agents = app_client.get("/api/works/WRK-001/agents").json()
    worktree_by_slug = {agent["slug"]: agent["worktree_path"] for agent in agents}
    assert worktree_by_slug[next_implementation_slug] == worktree_by_slug[implementation_slug]
    review_report = next(stage for stage in implementing["stages"] if stage["id"] == "code-review")[
        "reports"
    ][0]
    assert review_report["findings"] == ["Fix the race.", "Rename the fixture."]
    assert review_report["review_decision"] == {
        "decision": "send_back",
        "enforced_findings": [0],
        "instruction": "Use the shared lock helper.",
    }
    inputs = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", next_implementation_slug, 0
        )
        if event.get("type") == "user_input"
    ]
    # The contract is that a waived finding never reads as work to do -- not
    # that it is absent. The implementation declares the dismissed list so it
    # does not go and fix something the user deliberately let stand, so the
    # finding appears there and nowhere above it.
    prompt = inputs[-1]
    dismissed_at = prompt.index("Already dismissed by the user")
    actionable, dismissed = prompt[:dismissed_at], prompt[dismissed_at:]
    assert "Fix the race." in actionable
    assert "Rename the fixture." not in actionable
    assert "Rename the fixture." in dismissed
    assert "Use the shared lock helper." in prompt


def test_send_back_note_survives_stopping_and_retrying_the_stage(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    _, implementing = _send_back_from_a_human_review_gate(app_client, tmp_path)
    sent_back_slug = next(
        stage for stage in implementing["stages"] if stage["id"] == "implementation"
    )["agent_slug"]
    _wait_for_agent_idle(app_client, sent_back_slug)

    stopped = app_client.post("/api/works/WRK-001/runs/run-001/stop-stage")
    assert stopped.status_code == 200, stopped.text
    retried = app_client.post("/api/works/WRK-001/runs/run-001/retry-stage")
    assert retried.status_code == 200, retried.text

    retry_slug = next(
        stage for stage in retried.json()["stages"] if stage["id"] == "implementation"
    )["agent_slug"]
    assert retry_slug != sent_back_slug
    prompt = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", retry_slug, 0
        )
        if event.get("type") == "user_input"
    ][-1]
    # The retry launches a fresh agent, so the reason for the send-back has to
    # come from the loop rather than the prompt that carried it originally.
    assert "Use the shared lock helper." in prompt
    assert "Fix the race." in prompt


def test_human_review_gate_approve_as_is_completes_review(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    created = app_client.post(
        "/api/works",
        json={"name": "Approved review", "description": "Accept review findings."},
    )
    assert created.status_code == 201, created.text
    root = tmp_path / "repository"
    root.mkdir()
    definition = app_client.get("/api/loops/atelier-reviewed").json()
    started = app_client.post(
        "/api/works/WRK-001/runs",
        json={
            "goal": "Implement the reviewed change.",
            "root_path": str(root),
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
            "provider": "amp",
            "model": "smart",
            "options": {},
            "brief": {
                "goal": "Implement the reviewed change.",
                "stages": [
                    {
                        "stage_id": "code-review",
                        "note": "Check the public contract.",
                        "context": [],
                        "review_gate": "human_check",
                    }
                ],
            },
        },
    ).json()
    _append_stage_report(app_client, started["stages"][0]["agent_slug"], "pass", [])
    reviewing = _wait_for_stage(app_client, "code-review")
    review = next(stage for stage in reviewing["stages"] if stage["id"] == "code-review")
    _append_stage_report(
        app_client,
        review["agent_slug"],
        "changes_requested",
        ["This is acceptable for this release."],
    )
    _wait_for_status(app_client, "blocked_user")

    resumed = app_client.post(
        "/api/works/WRK-001/runs/run-001/resume",
        json={
            "gate_decision": "approve_as_is",
            "enforced_findings": [],
            "resolution_note": "",
        },
    )

    assert resumed.status_code == 200, resumed.text
    awaiting = _wait_for_status(app_client, "awaiting_approval")
    assert awaiting["current_stage_id"] == "approval"
    assert awaiting["review_gate"] is None
    completed_review = next(stage for stage in awaiting["stages"] if stage["id"] == "code-review")
    assert completed_review["status"] == "passed"
    report = completed_review["reports"][-1]
    assert report["outcome"] == "changes_requested"
    assert report["review_decision"] == {
        "decision": "approve_as_is",
        "enforced_findings": [],
        "instruction": "",
    }
    # Approving as-is dismisses every reported finding, not only the ones a
    # partial send-back left out, so a later reviewer is told not to raise it.
    assert awaiting["waived_findings_count"] == 1
    # The approval surface gets the findings themselves, not just a count, so
    # the user is reminded what they already let stand before approving again.
    assert awaiting["waived_findings"] == ["This is acceptable for this release."]


def test_the_run_surface_carries_every_feedback_record_and_its_state(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    """The run view shows what was asked for and whether it still stands, so
    the records reach the wire with their state rather than only the count."""
    started = _start_run(app_client, tmp_path)
    _append_stage_report(app_client, _first_agent_slug(started), "pass", [])
    _wait_for_status(app_client, "awaiting_approval")

    returned = app_client.post(
        "/api/works/WRK-001/runs/run-001/request-changes",
        json={"note": "Drop the SELECT FOR UPDATE."},
    )

    assert returned.status_code == 200, returned.text
    records = returned.json()["feedback"]
    assert [(item["source"], item["note"], item["state"]) for item in records] == [
        ("approval", "Drop the SELECT FOR UPDATE.", "open")
    ]


def test_accepting_a_run_dismisses_the_findings_it_let_stand(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    """Accepting is the user saying the result is good as it stands, so the
    findings still open when they accept are dismissed like `approve_as_is`
    dismisses them -- a later run is told not to raise them again."""
    started = _start_run(app_client, tmp_path)
    _append_stage_report(
        app_client,
        _first_agent_slug(started),
        "pass",
        ["The TODO in the launch path stays for now."],
    )
    _wait_for_status(app_client, "awaiting_approval")

    accepted = app_client.post("/api/works/WRK-001/runs/run-001/accept")

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["waived_findings_count"] == 1


def test_a_reviewer_is_told_which_findings_the_user_already_dismissed(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    """A finding the user waived at the gate must not come back: the next
    review reads it as dismissed rather than rediscovering it."""
    _, implementing = _send_back_from_a_human_review_gate(app_client, tmp_path)
    implementation = next(
        stage for stage in implementing["stages"] if stage["id"] == "implementation"
    )
    _append_stage_report(app_client, implementation["agent_slug"], "pass", [])
    reviewing = _wait_for_stage(app_client, "code-review")
    review = next(stage for stage in reviewing["stages"] if stage["id"] == "code-review")

    prompt = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", review["agent_slug"], 0
        )
        if event.get("type") == "user_input"
    ][-1]

    assert "Already dismissed by the user" in prompt
    assert "Rename the fixture." in prompt


def test_a_create_pr_send_back_fails_the_run_instead_of_reopening_it(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    started = _start_run(app_client, tmp_path, git=True)
    first_implementation = _first_agent_slug(started)
    _complete_and_accept(app_client, started)
    response = app_client.post(
        "/api/works/WRK-001/runs/run-001/create-pr",
        json={
            "name": "Complete the loop workflow",
            "provider": "amp",
            "model": "smart",
        },
    )
    assert response.status_code == 200, response.text
    creating_pr = _wait_for_stage(app_client, "create-pr")
    pr_stage = next(stage for stage in creating_pr["stages"] if stage["id"] == "create-pr")

    _append_stage_report(
        app_client,
        pr_stage["agent_slug"],
        "changes_requested",
        ["The focused test fails in the changed behavior."],
        validation_evidence="$ pytest tests/test_changed.py\n1 failed",
    )

    # Publishing is the last decision: the PR stage stops the run for a human
    # rather than starting another implementation pass nobody asked for.
    failed = _wait_for_status(app_client, "failed")
    assert failed["pass_number"] == 1
    pr_row = next(stage for stage in failed["stages"] if stage["id"] == "create-pr")
    assert pr_row["status"] == "failed"
    implementation = next(
        stage for stage in failed["stages"] if stage["id"] == "implementation"
    )
    assert implementation["agent_slug"] == first_implementation
    assert "cannot request changes" in failed["status_reason"]


def _append_stage_report(
    client: TestClient,
    agent_slug: str,
    outcome: str,
    findings: list[str],
    *,
    validation_evidence: str = "tests passed",
    artifact_refs: list[str] | None = None,
) -> None:
    """Append one complete loop report and idle marker for an active stage."""
    _wait_for_agent_idle(client, agent_slug)
    client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "message_complete",
            "text": json.dumps(
                {
                    "atelier_loop_step_report": {
                        "outcome": outcome,
                        "summary": "Stage completed.",
                        "findings": findings,
                        "changes": "Updated the code.",
                        "validation_evidence": validation_evidence,
                        "divergences": "None.",
                        "skipped_scope": "None.",
                        "blocker": "None.",
                        "artifact_refs": artifact_refs or [],
                    }
                }
            ),
        },
    )
    client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001", agent_slug, {"type": "status_change", "status": "idle"}
    )


def _start_run(client: TestClient, tmp_path: Path, *, git: bool = False) -> dict[str, object]:
    """Create one Work and start its default fast objective loop."""
    created = client.post(
        "/api/works",
        json={"name": "Loop work", "description": "Fix the launch path."},
    )
    assert created.status_code == 201, created.text
    root = tmp_path / "repository"
    root.mkdir()
    if git:
        subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        (root / "README.md").write_text("test repository\n")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    definition = client.get("/api/loops/atelier-fast").json()
    response = client.post(
        "/api/works/WRK-001/runs",
        json={
            "goal": "Implement the standalone loop endpoint.",
            "root_path": str(root),
            "loop_definition_id": definition["id"],
            "loop_revision": definition["revision"],
            "provider": "amp",
            "model": "smart",
            "options": {},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _first_agent_slug(run: dict[str, object]) -> str:
    """Return the first stage agent from an objective run response."""
    stages = run["stages"]
    assert isinstance(stages, list) and stages
    agent_slug = stages[0]["agent_slug"]
    assert isinstance(agent_slug, str)
    return agent_slug


def _wait_for_stage(client: TestClient, stage_id: str) -> dict[str, object]:
    """Wait until the objective monitor advances to one stage."""
    for _ in range(100):
        run = client.get("/api/works/WRK-001/runs/run-001").json()
        if run["current_stage_id"] == stage_id:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run did not advance to {stage_id}")


def _complete_and_accept(client: TestClient, run: dict[str, object]) -> dict[str, object]:
    """Submit a passing report and accept one started objective run."""
    stages = run["stages"]
    assert isinstance(stages, list)
    first = stages[0]
    assert isinstance(first, dict)
    agent_slug = first["agent_slug"]
    assert isinstance(agent_slug, str)
    _wait_for_agent_idle(client, agent_slug)
    client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {
            "type": "message_complete",
            "ts": "2026-07-13T00:00:00+00:00",
            "text": json.dumps(
                {
                    "atelier_loop_step_report": {
                        "outcome": "pass",
                        "summary": "Implemented the endpoint.",
                        "findings": [],
                        "changes": "Added the standalone run route.",
                        "validation_evidence": "integration test passed",
                        "divergences": "None.",
                        "skipped_scope": "None.",
                        "blocker": "None.",
                        "artifact_refs": [],
                    }
                }
            ),
        },
    )
    client.app.state.workstore.append_transcript_event_with_seq(
        "WRK-001",
        agent_slug,
        {"type": "status_change", "status": "idle"},
    )
    _wait_for_status(client, "awaiting_approval")
    response = client.post("/api/works/WRK-001/runs/run-001/accept")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_status(
    client: TestClient,
    status: str,
    timeout: float = 5.0,
) -> dict[str, object]:
    """Poll one objective run until its loop reaches ``status``."""
    deadline = time.monotonic() + timeout
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = client.get("/api/works/WRK-001/runs/run-001")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == status:
            return last
        time.sleep(0.05)
    raise AssertionError(f"objective run did not reach {status}: {last}")


def _wait_for_agent_idle(
    client: TestClient,
    agent_slug: str,
    timeout: float = 2.0,
) -> None:
    """Wait until the scripted adapter finishes its initial response."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = list(
            client.app.state.workstore.read_transcript_from_cursor(
                "WRK-001",
                agent_slug,
                0,
            )
        )
        statuses = [event.get("status") for event in events if event.get("type") == "status_change"]
        if statuses and statuses[-1] == "idle":
            return
        time.sleep(0.02)
    raise AssertionError(f"agent did not become idle: {agent_slug}")


def test_a_merged_pull_request_stops_the_run_from_being_retried(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    """A merged PR leaves the run nowhere to push, so it must not restart."""
    agent_slug = _fail_run_for_retry(app_client, tmp_path)
    assert agent_slug
    store = LoopRunStore(app_client.app.state.loop_runs)
    target = store.load("WRK-001", "run-001")
    assert target is not None
    pr_url = "https://github.com/acme/repo/pull/13"
    target.run["loop"]["pr"] = {"url": pr_url, "status": "open"}
    store.save(target)
    app_client.app.state.workstore.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            type="pr",
            title="Story 01",
            status="merged",
            url=pr_url,
        )
    )

    response = app_client.post("/api/works/WRK-001/runs/run-001/retry-stage")

    assert response.status_code == 422, response.text
    assert "merged" in response.text


def test_an_open_pull_request_leaves_the_retry_alone(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    agent_slug = _fail_run_for_retry(app_client, tmp_path)
    assert agent_slug
    store = LoopRunStore(app_client.app.state.loop_runs)
    target = store.load("WRK-001", "run-001")
    assert target is not None
    pr_url = "https://github.com/acme/repo/pull/13"
    target.run["loop"]["pr"] = {"url": pr_url, "status": "open"}
    store.save(target)
    app_client.app.state.workstore.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            type="pr",
            title="Story 01",
            status="open",
            url=pr_url,
        )
    )

    response = app_client.post("/api/works/WRK-001/runs/run-001/retry-stage")

    assert response.status_code == 200, response.text


def test_a_retry_note_reaches_the_new_agent_alongside_the_continuation_hint(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    """The note answers this attempt; the hint still explains the workspace."""
    _fail_run_for_retry(app_client, tmp_path)

    response = app_client.post(
        "/api/works/WRK-001/runs/run-001/retry-stage",
        json={"note": "Restore the parity test from fe901fbc before continuing."},
    )

    assert response.status_code == 200, response.text
    retry_slug = response.json()["stages"][0]["agent_slug"]
    prompt = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", retry_slug, 0
        )
        if event.get("type") == "user_input"
    ][-1]
    assert "Restore the parity test from fe901fbc before continuing." in prompt
    assert "previous attempt" in prompt


def test_a_retry_without_a_note_is_unchanged(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    _fail_run_for_retry(app_client, tmp_path)

    response = app_client.post("/api/works/WRK-001/runs/run-001/retry-stage", json={"note": "  "})

    assert response.status_code == 200, response.text
    retry_slug = response.json()["stages"][0]["agent_slug"]
    prompt = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", retry_slug, 0
        )
        if event.get("type") == "user_input"
    ][-1]
    # Nothing is inserted between the continuation hint and the report contract.
    assert "proceed normally.\n\nWhen this stage reaches a stopping point" in prompt


def test_selected_pr_feedback_still_starts_an_implementation_pass(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    """The PR stage's return edge is the user's, even though the agent may not
    use it: sending feedback back for implementation must keep working."""
    started = _start_run(app_client, tmp_path, git=True)
    _complete_and_accept(app_client, started)
    assert (
        app_client.post(
            "/api/works/WRK-001/runs/run-001/create-pr",
            json={"name": "Complete the loop workflow", "provider": "amp", "model": "smart"},
        ).status_code
        == 200
    )
    creating_pr = _wait_for_stage(app_client, "create-pr")
    pr_stage = next(stage for stage in creating_pr["stages"] if stage["id"] == "create-pr")
    _append_stage_report(
        app_client,
        pr_stage["agent_slug"],
        "pass",
        [],
        artifact_refs=["https://github.com/acme/repo/pull/13"],
    )
    _wait_for_status(app_client, "accepted")
    store = LoopRunStore(app_client.app.state.loop_runs)
    target = store.load("WRK-001", "run-001")
    assert target is not None
    pr_stage_row = next(
        row for row in target.run["loop"]["stages"] if row["id"] == "create-pr"
    )
    target.run["loop"]["pr_comments"] = [
        {
            "id": "PRRC_selected",
            "author": "reviewer",
            "body": "Drop the SELECT FOR UPDATE.",
            "location": "kernel/move_lpn.py:34",
            "created_at": "2126-01-01T00:00:00+00:00",
        },
        {
            "id": "PRRC_ignored",
            "author": "reviewer",
            "body": "Praise: nice work on the race.",
            "created_at": "2126-01-01T00:00:00+00:00",
        },
    ]
    pr_stage_row["push_at"] = "2026-01-01T00:00:00+00:00"
    store.save(target)

    response = app_client.post(
        "/api/works/WRK-001/runs/run-001/pr-feedback",
        json={
            "comments": [{"comment_id": "PRRC_selected", "instruction": "Use the lock helper."}],
            "instruction": "The reviewer wants the lock removed.",
        },
    )

    assert response.status_code == 200, response.text
    implementing = _wait_for_stage(app_client, "implementation")
    assert implementing["status"] == "running"
    implementation = next(
        stage for stage in implementing["stages"] if stage["id"] == "implementation"
    )
    prompt = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", implementation["agent_slug"], 0
        )
        if event.get("type") == "user_input"
    ][-1]
    assert "The reviewer wants the lock removed." in prompt
    # The selected comment, its per-comment instruction, and nothing else.
    assert "Drop the SELECT FOR UPDATE." in prompt
    # The decision that opened this pass is in the PR stage's ledger, so the
    # run's account does not skip the event that caused the pass. It pushed
    # nothing, so it claims no push and no addressed comments.
    pr_reports = next(
        stage for stage in implementing["stages"] if stage["id"] == "create-pr"
    )["reports"]
    assert pr_reports[-1]["outcome"] == "changes_requested"
    assert "Drop the SELECT FOR UPDATE." in pr_reports[-1]["summary"]
    assert pr_reports[-1]["push_at"] is None
    assert pr_reports[-1]["addressed_comments"] == []
    assert pr_reports[-2]["outcome"] == "pass"
    assert pr_reports[-2]["push_at"] is not None
    assert "Use the lock helper." in prompt
    assert "Praise: nice work on the race." not in prompt

    # The pass that answers the feedback reaches Create PR again. The stage
    # publishes; it must be told what this push answers, not ordered to act on
    # the comment itself. Ordering it made it report changes_requested, which
    # reopened the very pass that produced the request, and the run cycled.
    _append_stage_report(app_client, implementation["agent_slug"], "pass", [])
    _wait_for_status(app_client, "awaiting_approval")
    assert app_client.post("/api/works/WRK-001/runs/run-001/accept").status_code == 200
    publishing = _wait_for_stage(app_client, "create-pr")
    publisher = next(stage for stage in publishing["stages"] if stage["id"] == "create-pr")
    pr_prompt = [
        event["text"]
        for event in app_client.app.state.workstore.read_transcript_from_cursor(
            "WRK-001", publisher["agent_slug"], 0
        )
        if event.get("type") == "user_input"
    ][-1]
    assert "Addressed in this push:" in pr_prompt
    assert "Drop the SELECT FOR UPDATE." in pr_prompt
    assert "Address this pull-request feedback in the current worktree" not in pr_prompt


def test_approving_a_gate_as_is_keeps_the_reviewer_findings_visible(
    app_client: TestClient,
    tmp_path: Path,
) -> None:
    """Narrowing the run's account to the enforced findings is right when work
    is sent back. On approve-as-is nothing is being corrected, and emptying it
    left the run view showing a review that had found nothing."""
    _, implementing = _send_back_from_a_human_review_gate(app_client, tmp_path)
    review = next(stage for stage in implementing["stages"] if stage["id"] == "code-review")

    # The send-back narrowed the live account to the one enforced finding while
    # the occurrence ledger kept both.
    assert review["findings"] == ["Fix the race."]
    assert review["reports"][0]["findings"] == ["Fix the race.", "Rename the fixture."]
    assert len(review["finding_details"]) == len(review["findings"])
