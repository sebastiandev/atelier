"""Integration tests for /api/chats.

The chat flow is intentionally lightweight, but promotion crosses several
boundaries: SQL chat row, chat transcript files, WorkStore creation, and
work-scoped context files.
"""

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from src.domain.agents import AgentStartContext
from src.domain.agents.compactions import (
    BreadcrumbResult,
    CompactionSessionStartResult,
)
from src.domain.agents.configs import (
    AmpAgentConfig,
    AmpPermissionMode,
    CodexAcpAgentConfig,
    CodexAcpEffort,
    CodexAcpMode,
)
from src.domain.commands.chats import send_input as send_chat_input
from src.domain.commands.chats.connect import build_chat_runtime_config
from src.domain.commands.planning import (
    mark_ready as planning_mark_ready,
)
from src.domain.commands.planning import (
    submit_materialization,
)
from src.domain.planning.dtos import PlanArtifactEntry
from src.settings import Settings


def _new_chat(first_message: str = "Can we explore a smaller launch plan?") -> dict:
    return {
        "provider": "codex",
        "model": "gpt-5.4",
        "first_message": first_message,
    }


def _new_amp_chat(first_message: str = "Can we explore a smaller launch plan?") -> dict:
    return {
        "provider": "amp",
        "model": "smart",
        "first_message": first_message,
    }


def _new_codex_acp_chat(
    first_message: str = "Can we explore a smaller launch plan?",
) -> dict:
    return {
        "provider": "codex-acp",
        "model": "gpt-5.5",
        "first_message": first_message,
    }


def _new_project(name: str = "Atelier", glyph: str = "AT") -> dict[str, object]:
    return {"name": name, "description": "", "glyph": glyph, "color": 250}


class _FakeCompactionSessionClient:
    def __init__(self) -> None:
        self.summary_prompt: str | None = None
        self.seed_message: str | None = None
        self.breadcrumb: str | None = None

    async def summarize_transcript(
        self,
        *,
        config: Any,
        context: AgentStartContext,
        prompt: str,
    ) -> str:
        assert context.session_id is None
        self.summary_prompt = prompt
        return (
            "## Goal\n"
            "Keep exploring the runtime chat plan.\n\n"
            "## Decisions\n"
            "Use chat compaction before long follow-up turns.\n\n"
            "## Key files\n"
            "No key files.\n\n"
            "## Blockers\n"
            "No blockers."
        )

    async def start_fresh_session(
        self,
        *,
        config: Any,
        context: AgentStartContext,
        seed_message: str,
    ) -> CompactionSessionStartResult:
        assert context.session_id is None
        self.seed_message = seed_message
        return CompactionSessionStartResult(session_id="new-chat-session")

    async def write_breadcrumb(
        self,
        *,
        config: Any,
        context: AgentStartContext,
        old_session_id: str,
        breadcrumb: str,
    ) -> BreadcrumbResult:
        assert old_session_id == "old-chat-session"
        self.breadcrumb = breadcrumb
        return BreadcrumbResult(written=True)


class _CapturingChatSupervisor:
    def __init__(self) -> None:
        self.inputs: list[tuple[str, str, str | None]] = []

    async def send_input(
        self,
        chat_slug: str,
        text: str,
        *,
        transcript_text: str | None = None,
        record_user_input: bool = True,
    ) -> None:
        assert record_user_input is True
        self.inputs.append((chat_slug, text, transcript_text))


def test_create_chat_persists_metadata_and_transcript(
    app_client: TestClient, test_settings: Settings
) -> None:
    response = app_client.post("/api/chats", json=_new_chat())
    assert response.status_code == 201
    body = response.json()

    assert body["slug"] == "CHT-001"
    assert body["title"] == "Can we explore a smaller launch plan?"
    assert body["grounding"] is None
    assert [m["role"] for m in body["transcript"]] == ["user"]
    assert body["message_count"] == 1

    chat_dir = test_settings.workspace_root / "chats" / "CHT-001"
    chat_json = json.loads((chat_dir / "chat.json").read_text())
    assert chat_json["slug"] == "CHT-001"
    transcript = (chat_dir / "transcript.ndjson").read_text().splitlines()
    assert len(transcript) == 1


def test_create_chat_separates_link_from_working_folder(
    app_client: TestClient, test_settings: Settings
) -> None:
    work = app_client.post(
        "/api/works",
        json={"name": "Existing work", "description": "active", "contexts": []},
    ).json()
    working_dir = test_settings.workspace_root / "scratch"
    working_dir.mkdir(parents=True)

    response = app_client.post(
        "/api/chats",
        json={
            **_new_chat("Explore this work from a real checkout"),
            "grounding": {"kind": "work", "ref": work["slug"]},
            "working_directory": str(working_dir),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["grounding"] == {"kind": "work", "ref": work["slug"]}
    assert body["working_directory"] == str(working_dir)
    chat_json = json.loads(
        (test_settings.workspace_root / "chats" / "CHT-001" / "chat.json").read_text()
    )
    assert chat_json["working_directory"] == str(working_dir)

    record = app_client.app.state.chatstore.get_chat("CHT-001")
    assert record is not None
    _config, context, runtime = build_chat_runtime_config(
        record,
        app_client.app.state.workstore,
        app_client.app.state.projectstore,
        app_client.app.state.planningfiles,
        test_settings,
    )
    assert context.workdir == working_dir
    assert runtime.workdir == working_dir
    assert runtime.link_label.startswith(f"work {work['slug']}")


def test_planning_chat_runtime_prompt_uses_setup_contract(
    app_client: TestClient, test_settings: Settings
) -> None:
    work = app_client.post(
        "/api/works",
        json={"name": "Existing work", "description": "active", "contexts": []},
    ).json()
    working_dir = test_settings.workspace_root / "scratch"
    working_dir.mkdir(parents=True)
    response = app_client.post(
        "/api/chats",
        json={
            **_new_chat("Initialize an Atelier Planning session."),
            "title": "Planning",
            "grounding": {"kind": "work", "ref": work["slug"]},
            "working_directory": str(working_dir),
        },
    )
    assert response.status_code == 201

    record = app_client.app.state.chatstore.get_chat("CHT-001")
    assert record is not None
    _config, context, _runtime = build_chat_runtime_config(
        record,
        app_client.app.state.workstore,
        app_client.app.state.projectstore,
        app_client.app.state.planningfiles,
        test_settings,
    )

    assert "Atelier's Planning chat" in context.system_prompt
    assert "Discovery phase:" in context.system_prompt
    assert "Available framework roles/lenses" in context.system_prompt
    assert "Ready to create source plan" in context.system_prompt
    assert "atelier_planning_ready" in context.system_prompt


def test_planning_chat_revision_prompt_lists_source_documents(
    app_client: TestClient, test_settings: Settings
) -> None:
    work = app_client.post(
        "/api/works",
        json={"name": "Existing work", "description": "active", "contexts": []},
    ).json()
    working_dir = test_settings.workspace_root / "scratch"
    (working_dir / ".bmad-core").mkdir(parents=True)
    planning_dir = working_dir / ".atelier" / "planning" / work["slug"]
    stories_dir = planning_dir / "stories"
    stories_dir.mkdir(parents=True)
    (planning_dir / "intent.md").write_text("# Intent\n")
    (stories_dir / "story-001.md").write_text("# First Story\n")
    response = app_client.post(
        "/api/chats",
        json={
            **_new_codex_acp_chat("Initialize an Atelier Planning session."),
            "title": "Planning",
            "grounding": {"kind": "work", "ref": work["slug"]},
            "working_directory": str(working_dir),
            "options": {"reasoning_effort": "xhigh", "mode": "read-only"},
        },
    )
    assert response.status_code == 201
    submit_materialization.execute(
        app_client.app.state.workstore,
        app_client.app.state.planningfiles,
        submit_materialization.SubmitPlanMaterializationRequest(
            work_slug=work["slug"],
            root_path=str(working_dir),
            framework="bmad",
            profile="feature",
            artifacts=(
                PlanArtifactEntry(
                    path="intent.md",
                    title="Intent",
                    artifact_kind="brief",
                    executable=False,
                    dependencies=(),
                ),
                PlanArtifactEntry(
                    path="stories/story-001.md",
                    title="First Story",
                    artifact_kind="story",
                    executable=True,
                    dependencies=("intent.md",),
                ),
            ),
        ),
    )

    record = app_client.app.state.chatstore.get_chat(response.json()["slug"])
    assert record is not None
    config, context, runtime = build_chat_runtime_config(
        record,
        app_client.app.state.workstore,
        app_client.app.state.projectstore,
        app_client.app.state.planningfiles,
        test_settings,
    )

    assert "Revision phase:" in context.system_prompt
    assert "@intent.md" in context.system_prompt
    assert "@stories/story-001.md" in context.system_prompt
    assert runtime.agent_workdir == planning_dir
    assert runtime.writable_roots == (planning_dir,)
    assert isinstance(config, CodexAcpAgentConfig)
    assert config.reasoning_effort is CodexAcpEffort.XHIGH
    assert config.mode is CodexAcpMode.AUTO


def test_planning_chat_input_sends_hidden_current_document_index(
    app_client: TestClient, test_settings: Settings
) -> None:
    work = app_client.post(
        "/api/works",
        json={"name": "Existing work", "description": "active", "contexts": []},
    ).json()
    working_dir = test_settings.workspace_root / "scratch"
    (working_dir / ".bmad-core").mkdir(parents=True)
    planning_dir = working_dir / ".atelier" / "planning" / work["slug"]
    stories_dir = planning_dir / "stories"
    stories_dir.mkdir(parents=True)
    (planning_dir / "intent.md").write_text("# Intent\n")
    (stories_dir / "story-001.md").write_text("# First Story\n")
    response = app_client.post(
        "/api/chats",
        json={
            **_new_chat("Initialize an Atelier Planning session."),
            "title": "Planning",
            "grounding": {"kind": "work", "ref": work["slug"]},
            "working_directory": str(working_dir),
        },
    )
    assert response.status_code == 201
    submit_materialization.execute(
        app_client.app.state.workstore,
        app_client.app.state.planningfiles,
        submit_materialization.SubmitPlanMaterializationRequest(
            work_slug=work["slug"],
            root_path=str(working_dir),
            framework="bmad",
            profile="feature",
            artifacts=(
                PlanArtifactEntry(
                    path="intent.md",
                    title="Intent",
                    artifact_kind="brief",
                    executable=False,
                    dependencies=(),
                ),
                PlanArtifactEntry(
                    path="stories/story-001.md",
                    title="First Story",
                    artifact_kind="story",
                    executable=True,
                    dependencies=("intent.md",),
                ),
            ),
        ),
    )
    supervisor = _CapturingChatSupervisor()

    asyncio.run(
        send_chat_input.execute(
            app_client.app.state.chatstore,
            supervisor,  # type: ignore[arg-type]
            app_client.app.state.planningfiles,
            send_chat_input.SendChatInputRequest(
                chat_slug=response.json()["slug"],
                text="What should we change next?",
            ),
        )
    )

    assert len(supervisor.inputs) == 1
    chat_slug, provider_text, transcript_text = supervisor.inputs[0]
    assert chat_slug == response.json()["slug"]
    assert transcript_text == "What should we change next?"
    assert "<atelier_planning_context>" in provider_text
    assert f"Source-backed planning folder: {planning_dir}" in provider_text
    assert "@intent.md" in provider_text
    assert "@stories/story-001.md" in provider_text
    assert "<user_message>\nWhat should we change next?\n</user_message>" in provider_text


def test_planning_chat_summary_exposes_readiness(
    app_client: TestClient, test_settings: Settings
) -> None:
    work = app_client.post(
        "/api/works",
        json={"name": "Existing work", "description": "active", "contexts": []},
    ).json()
    working_dir = test_settings.workspace_root / "scratch"
    working_dir.mkdir(parents=True)
    response = app_client.post(
        "/api/chats",
        json={
            **_new_chat("Initialize an Atelier Planning session."),
            "title": "Planning",
            "grounding": {"kind": "work", "ref": work["slug"]},
            "working_directory": str(working_dir),
        },
    )
    assert response.status_code == 201

    result = planning_mark_ready.execute(
        app_client.app.state.chatstore,
        planning_mark_ready.MarkPlanningChatReadyRequest(
            chat_slug=response.json()["slug"],
            assistant_text=(
                "Ready to create source plan.\n"
                '{"atelier_planning_ready":{"ready":true,"summary":"Enough scope"}}'
            ),
        ),
    )

    assert result is not None
    assert result.changed is True
    summary_response = app_client.get(f"/api/chats?work_slug={work['slug']}")
    assert summary_response.status_code == 200
    summary = summary_response.json()[0]
    assert summary["planning_readiness"] == {
        "ready": True,
        "summary": "Enough scope",
    }


def test_planning_readiness_metadata_is_not_sent_to_provider_config(
    app_client: TestClient, test_settings: Settings
) -> None:
    work = app_client.post(
        "/api/works",
        json={"name": "Existing work", "description": "active", "contexts": []},
    ).json()
    working_dir = test_settings.workspace_root / "scratch"
    working_dir.mkdir(parents=True)
    response = app_client.post(
        "/api/chats",
        json={
            **_new_codex_acp_chat("Initialize an Atelier Planning session."),
            "title": "Planning",
            "grounding": {"kind": "work", "ref": work["slug"]},
            "working_directory": str(working_dir),
            "options": {"reasoning_effort": "xhigh", "mode": "read-only"},
        },
    )
    assert response.status_code == 201

    result = planning_mark_ready.execute(
        app_client.app.state.chatstore,
        planning_mark_ready.MarkPlanningChatReadyRequest(
            chat_slug=response.json()["slug"],
            assistant_text=(
                "Ready to create source plan.\n"
                '{"atelier_planning_ready":{"ready":true,"summary":"Enough scope"}}'
            ),
        ),
    )
    assert result is not None

    record = app_client.app.state.chatstore.get_chat(response.json()["slug"])
    assert record is not None
    assert "planning_readiness" in (record.chat.options or {})
    config, _context, _runtime = build_chat_runtime_config(
        record,
        app_client.app.state.workstore,
        app_client.app.state.projectstore,
        app_client.app.state.planningfiles,
        test_settings,
    )

    assert isinstance(config, CodexAcpAgentConfig)
    assert config.reasoning_effort is CodexAcpEffort.XHIGH
    assert config.mode is CodexAcpMode.READ_ONLY


def test_create_chat_persists_provider_options_for_runtime(
    app_client: TestClient, test_settings: Settings
) -> None:
    response = app_client.post(
        "/api/chats",
        json={
            **_new_amp_chat("Use relaxed permissions for exploration"),
            "options": {"permission_mode": "allow_all"},
        },
    )

    assert response.status_code == 201
    chat_json = json.loads(
        (test_settings.workspace_root / "chats" / "CHT-001" / "chat.json").read_text()
    )
    assert chat_json["options"] == {"permission_mode": "allow_all"}

    record = app_client.app.state.chatstore.get_chat("CHT-001")
    assert record is not None
    config, _context, _runtime = build_chat_runtime_config(
        record,
        app_client.app.state.workstore,
        app_client.app.state.projectstore,
        app_client.app.state.planningfiles,
        test_settings,
    )
    assert isinstance(config, AmpAgentConfig)
    assert config.permission_mode is AmpPermissionMode.ALLOW_ALL


def test_set_chat_option_updates_chat_json_and_runtime_config(
    app_client: TestClient, test_settings: Settings
) -> None:
    app_client.post(
        "/api/chats",
        json={
            **_new_amp_chat("Tune chat effort"),
            "options": {"permission_mode": "default"},
        },
    )

    app_client.app.state.chatstore.set_chat_option(
        "CHT-001", "permission_mode", "allow_all"
    )

    chat_json = json.loads(
        (test_settings.workspace_root / "chats" / "CHT-001" / "chat.json").read_text()
    )
    assert chat_json["options"] == {"permission_mode": "allow_all"}

    record = app_client.app.state.chatstore.get_chat("CHT-001")
    assert record is not None
    config, _context, _runtime = build_chat_runtime_config(
        record,
        app_client.app.state.workstore,
        app_client.app.state.projectstore,
        app_client.app.state.planningfiles,
        test_settings,
    )
    assert isinstance(config, AmpAgentConfig)
    assert config.permission_mode is AmpPermissionMode.ALLOW_ALL


def test_create_chat_rejects_invalid_provider_options(
    app_client: TestClient,
) -> None:
    response = app_client.post(
        "/api/chats",
        json={
            **_new_amp_chat("Bad options should fail early"),
            "options": {"unknown": "value"},
        },
    )

    assert response.status_code == 422
    assert "unknown options" in response.json()["detail"]


def test_send_chat_message_appends_user_and_assistant_turns(
    app_client: TestClient,
) -> None:
    app_client.post("/api/chats", json=_new_chat())
    response = app_client.post(
        "/api/chats/CHT-001/messages",
        json={"body": "What risks should become action items?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [m["role"] for m in body["transcript"]] == [
        "user",
        "user",
        "assistant",
    ]
    assert body["message_count"] == 3


def test_patch_chat_renames_title_in_db_and_chat_json(
    app_client: TestClient, test_settings: Settings
) -> None:
    app_client.post("/api/chats", json=_new_chat("Original chat title"))

    response = app_client.patch(
        "/api/chats/CHT-001",
        json={"title": "Renamed chat"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed chat"
    assert body["message_count"] == 1
    chat_json = json.loads(
        (test_settings.workspace_root / "chats" / "CHT-001" / "chat.json").read_text()
    )
    assert chat_json["title"] == "Renamed chat"


def test_delete_chat_removes_metadata_and_files(
    app_client: TestClient, test_settings: Settings
) -> None:
    app_client.post("/api/chats", json=_new_chat("Delete this chat"))
    chat_dir = test_settings.workspace_root / "chats" / "CHT-001"
    assert chat_dir.exists()

    response = app_client.delete("/api/chats/CHT-001")

    assert response.status_code == 204
    assert not chat_dir.exists()
    assert app_client.get("/api/chats/CHT-001").status_code == 404
    assert app_client.get("/api/chats").json() == []


def test_chat_ws_replays_initial_prompt_and_records_later_input(
    app_client: TestClient,
) -> None:
    app_client.post("/api/chats", json=_new_amp_chat("Think through runtime chat"))

    with app_client.websocket_connect("/api/chats/CHT-001/stream") as ws:
        events = [ws.receive_json() for _ in range(16)]

        assert events[0]["type"] == "user_input"
        assert events[0]["text"] == "Think through runtime chat"
        assert events[1]["type"] == "status_change"
        assert events[1]["status"] == "thinking"
        assert events[-1]["type"] == "status_change"
        assert events[-1]["status"] == "idle"

        ws.send_text(json.dumps({"type": "input", "text": "continue"}))
        ev = ws.receive_json()
        assert ev["type"] == "user_input"
        assert ev["text"] == "continue"

    chat = app_client.get("/api/chats/CHT-001").json()
    assert [m["role"] for m in chat["transcript"][:2]] == ["user", "assistant"]


def test_promote_chat_creates_work_with_context_file(
    app_client: TestClient, test_settings: Settings
) -> None:
    app_client.post("/api/chats", json=_new_chat("Build a launch checklist"))

    response = app_client.post(
        "/api/chats/CHT-001/promote",
        json={
            "name": "Launch checklist",
            "description": "- Define the smallest shippable version\n- Verify rollout risk",
        },
    )

    assert response.status_code == 200
    work = response.json()
    assert work["slug"] == "WRK-001"
    assert work["from_chat"] == {
        "slug": "CHT-001",
        "title": "Build a launch checklist",
    }
    assert work["chat_context_folders"][0]["name"] == "cht-001-context"
    assert work["chat_context_folders"][0]["chat_slug"] == "CHT-001"

    work_json = json.loads(
        (test_settings.workspace_root / "works" / "WRK-001" / "work.json").read_text()
    )
    assert work_json["from_chat"]["chat_slug"] == "CHT-001"
    assert work_json["chat_context_folders"][0]["mount_path"] == "cht-001-context"

    doc_response = app_client.get(
        "/api/works/WRK-001/chat-contexts/cht-001-context/context.md"
    )
    assert doc_response.status_code == 200
    doc = doc_response.json()
    assert "chat://CHT-001" in doc["content"]
    assert "- Define the smallest shippable version" in doc["content"]

    chat = app_client.get("/api/chats/CHT-001").json()
    assert chat["promoted_to_work_slug"] == "WRK-001"


def test_ensure_work_chat_context_creates_and_reuses_file(
    app_client: TestClient, test_settings: Settings
) -> None:
    work = app_client.post(
        "/api/works",
        json={"name": "Existing work", "description": "active", "contexts": []},
    ).json()
    app_client.post(
        "/api/chats",
        json={
            **_new_chat("Turn this chat into agent context"),
            "grounding": {"kind": "work", "ref": work["slug"]},
        },
    )

    response = app_client.post("/api/works/WRK-001/chats/CHT-001/context")

    assert response.status_code == 200
    folder = response.json()
    assert folder["name"] == "cht-001-context"
    assert folder["chat_slug"] == "CHT-001"
    assert folder["context_filename"] == "context.md"
    context_path = (
        test_settings.workspace_root
        / "works"
        / "WRK-001"
        / "chat-contexts"
        / "cht-001-context"
        / "context.md"
    )
    assert context_path.exists()
    assert "chat://CHT-001" in context_path.read_text()

    second = app_client.post("/api/works/WRK-001/chats/CHT-001/context")
    assert second.status_code == 200
    detail = app_client.get("/api/works/WRK-001").json()
    assert [f["chat_slug"] for f in detail["chat_context_folders"]] == ["CHT-001"]


def test_work_scoped_listing_includes_grounded_and_promoted_chats(
    app_client: TestClient,
) -> None:
    work = app_client.post(
        "/api/works",
        json={"name": "Existing work", "description": "active", "contexts": []},
    ).json()
    app_client.post(
        "/api/chats",
        json={
            **_new_chat("Explore the existing work"),
            "grounding": {"kind": "work", "ref": work["slug"]},
        },
    )
    app_client.post("/api/chats", json=_new_chat("Promote this later"))
    app_client.post(
        "/api/chats/CHT-002/promote",
        json={"name": "Promoted work", "description": "summary"},
    )

    response = app_client.get(f"/api/chats?work_slug={work['slug']}")
    assert response.status_code == 200
    assert [c["slug"] for c in response.json()] == ["CHT-001"]

    promoted = app_client.get("/api/chats?work_slug=WRK-002").json()
    assert [c["slug"] for c in promoted] == ["CHT-002"]


def test_root_chat_listing_only_includes_unassigned_chats(
    app_client: TestClient,
) -> None:
    app_client.post("/api/projects", json=_new_project())
    work = app_client.post(
        "/api/works",
        json={
            "name": "Project work",
            "description": "active",
            "contexts": [],
            "project_slug": "PRJ-001",
        },
    ).json()
    app_client.post("/api/chats", json=_new_chat("Loose exploration"))
    app_client.post(
        "/api/chats",
        json={
            **_new_chat("Project-level exploration"),
            "grounding": {"kind": "project", "ref": "PRJ-001"},
        },
    )
    app_client.post(
        "/api/chats",
        json={
            **_new_chat("Work-level exploration"),
            "grounding": {"kind": "work", "ref": work["slug"]},
        },
    )
    app_client.post("/api/chats", json=_new_chat("Promote this chat"))
    app_client.post(
        "/api/chats/CHT-004/promote",
        json={"name": "Promoted work", "description": "summary"},
    )

    response = app_client.get("/api/chats")

    assert response.status_code == 200
    assert [c["slug"] for c in response.json()] == ["CHT-001"]


def test_project_scoped_listing_excludes_work_and_promoted_chats(
    app_client: TestClient,
) -> None:
    app_client.post("/api/projects", json=_new_project())
    work = app_client.post(
        "/api/works",
        json={
            "name": "Project work",
            "description": "active",
            "contexts": [],
            "project_slug": "PRJ-001",
        },
    ).json()
    app_client.post(
        "/api/chats",
        json={
            **_new_chat("Project-level exploration"),
            "grounding": {"kind": "project", "ref": "PRJ-001"},
        },
    )
    app_client.post(
        "/api/chats",
        json={
            **_new_chat("Work-level exploration"),
            "grounding": {"kind": "work", "ref": work["slug"]},
        },
    )
    app_client.post(
        "/api/chats",
        json={
            **_new_chat("Project chat later promoted"),
            "grounding": {"kind": "project", "ref": "PRJ-001"},
        },
    )
    app_client.post(
        "/api/chats/CHT-003/promote",
        json={
            "name": "Promoted project chat",
            "description": "summary",
            "project_slug": "PRJ-001",
        },
    )

    response = app_client.get("/api/chats?project_slug=PRJ-001")

    assert response.status_code == 200
    assert [c["slug"] for c in response.json()] == ["CHT-001"]


def test_compact_chat_replaces_session_and_records_boundary(
    app_client: TestClient,
    test_settings: Settings,
) -> None:
    app_client.post(
        "/api/chats",
        json=_new_amp_chat("Explore long chat context"),
    )
    fake_session = _FakeCompactionSessionClient()
    app_client.app.state.compaction_session_client = fake_session
    app_client.app.state.chatstore.set_chat_session_id(
        "CHT-001", "old-chat-session"
    )
    app_client.app.state.chatstore.append_transcript_event_with_seq(
        "CHT-001",
        {
            "type": "status_change",
            "ts": "2026-06-03T10:00:00+00:00",
            "status": "idle",
        },
    )
    app_client.app.state.chatstore.append_transcript_event_with_seq(
        "CHT-001",
        {
            "type": "message_complete",
            "ts": "2026-06-03T10:00:01+00:00",
            "text": "A first answer",
        },
    )

    response = app_client.post("/api/chats/CHT-001/compact", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["chat_slug"] == "CHT-001"
    assert body["old_session_id"] == "old-chat-session"
    assert body["new_session_id"] == "new-chat-session"
    assert "/compactions/" in body["summary_path"]
    assert body["summary_path"].endswith(".md")
    assert body["breadcrumb_written"] is True
    assert fake_session.summary_prompt is not None
    assert "[user] Explore long chat context" in fake_session.summary_prompt
    assert fake_session.seed_message is not None
    assert "<COMPACTED_CONTEXT>" in fake_session.seed_message
    assert fake_session.breadcrumb is not None
    assert "new-chat-session" in fake_session.breadcrumb

    chat_json = json.loads(
        (
            test_settings.workspace_root
            / "chats"
            / "CHT-001"
            / "chat.json"
        ).read_text()
    )
    assert chat_json["session_id"] == "new-chat-session"
    transcript_rows = [
        json.loads(line)
        for line in (
            test_settings.workspace_root
            / "chats"
            / "CHT-001"
            / "transcript.ndjson"
        ).read_text().splitlines()
    ]
    event_types = [row.get("type") for row in transcript_rows if "type" in row]
    assert event_types[-7:] == [
        "compaction_requested",
        "compaction_progress",
        "compaction_summary_created",
        "compaction_progress",
        "compaction_progress",
        "compaction_old_session_breadcrumb",
        "context_compacted",
    ]
    assert [
        row["phase"]
        for row in transcript_rows
        if row.get("type") == "compaction_progress"
    ] == ["summarizing", "starting_session", "linking_session"]
    assert transcript_rows[-1]["new_session_id"] == "new-chat-session"


def test_get_chat_compaction_summary_returns_saved_doc(
    app_client: TestClient,
) -> None:
    app_client.post("/api/chats", json=_new_chat("Summarize this"))
    app_client.app.state.chatstore.write_chat_compaction_doc(
        "CHT-001",
        "20260603-120000.md",
        "# Chat compaction\n\nSeed context.",
    )

    response = app_client.get(
        "/api/chats/CHT-001/compactions/20260603-120000.md"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["chat_slug"] == "CHT-001"
    assert body["filename"] == "20260603-120000.md"
    assert body["summary_path"].endswith("/compactions/20260603-120000.md")
    assert body["content"] == "# Chat compaction\n\nSeed context."


def test_get_chat_compaction_summary_404_for_missing_doc(
    app_client: TestClient,
) -> None:
    app_client.post("/api/chats", json=_new_chat("Summarize this"))

    response = app_client.get("/api/chats/CHT-001/compactions/missing.md")

    assert response.status_code == 404
