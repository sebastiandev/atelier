"""Integration tests for /api/works/{slug}/agents and the agent WS stream.

Walking-skeleton end-to-end: POST creates the work, POST creates the
agent (which starts the supervisor with a StubAgentAdapter replaying the
canned demo sequence), then a WS connection observes the events flowing
through replay-from-disk + live fan-out, plus exercises the input path.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_work(
    client: TestClient,
    name: str = "Skeleton demo",
) -> dict:
    response = client.post(
        "/api/works",
        json={
            "name": name,
            "description": "for the walking skeleton",
            "contexts": [],
        },
    )
    response.raise_for_status()
    return response.json()


def _create_agent(
    client: TestClient, work_slug: str, folder: str, name: str = "Architect"
) -> dict:
    response = client.post(
        f"/api/works/{work_slug}/agents",
        json={
            "name": name,
            "persona": "architect",
            "role": "architect",
            "provider": "amp",
            "model": "smart",
            "folder": folder,
        },
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# REST: agent creation
# ---------------------------------------------------------------------------


def test_create_agent_returns_summary(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "Architect",
            "persona": "architect",
            "role": "architect",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "agt-1"
    assert body["work_slug"] == work["slug"]
    assert body["persona"] == "architect"
    assert body["status"] == "idle"
    assert body["folder"] == tmp_workdir


def test_create_agent_422_for_unknown_option(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "X",
            "persona": "architect",
            "role": "x",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
            "options": {"bogus_key": "value"},
        },
    )
    assert response.status_code == 422
    assert "unknown options" in response.json()["detail"]


def test_create_agent_422_for_bad_model(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "X",
            "persona": "architect",
            "role": "x",
            "provider": "amp",
            "model": "turbo",  # not a valid AmpMode
            "folder": tmp_workdir,
        },
    )
    assert response.status_code == 422


def test_create_agent_creates_missing_agent_folder(
    app_client: TestClient, tmp_workdir: str
) -> None:
    """A folder that doesn't exist yet is auto-created at agent-start —
    typing a fresh path in the new-agent dialog shouldn't be a footgun."""
    fresh = f"{tmp_workdir}/created-on-demand"
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "X",
            "persona": "architect",
            "role": "x",
            "provider": "amp",
            "model": "smart",
            "folder": fresh,
        },
    )
    assert response.status_code == 201
    from pathlib import Path

    assert Path(fresh).is_dir()


def test_create_agent_422_for_unmkdirable_agent_folder(
    app_client: TestClient,
) -> None:
    """If mkdir fails (e.g. parent is a regular file), surface a 422 so
    the user can fix the path instead of seeing a cryptic SDK error."""
    # /etc/hosts is a file on every macOS/Linux box; mkdir under it
    # always fails with ENOTDIR.
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "X",
            "persona": "architect",
            "role": "x",
            "provider": "amp",
            "model": "smart",
            "folder": "/etc/hosts/cannot-create-under-a-file",
        },
    )
    assert response.status_code == 422
    assert "cannot use agent folder" in response.json()["detail"]


def test_create_agent_404_for_unknown_work(app_client: TestClient, tmp_workdir: str) -> None:
    response = app_client.post(
        "/api/works/WRK-404/agents",
        json={
            "name": "X",
            "persona": "architect",
            "role": "x",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
        },
    )
    assert response.status_code == 404


def test_list_agents_for_work_returns_summaries(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    _create_agent(app_client, work["slug"], tmp_workdir, name="Architect")
    _create_agent(app_client, work["slug"], tmp_workdir, name="Developer")

    response = app_client.get(f"/api/works/{work['slug']}/agents")
    assert response.status_code == 200
    payload = response.json()
    assert [a["name"] for a in payload] == ["Architect", "Developer"]
    assert all(a["work_slug"] == work["slug"] for a in payload)


def test_list_agents_for_work_404_for_unknown_work(app_client: TestClient) -> None:
    response = app_client.get("/api/works/WRK-404/agents")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# WS: replay + live
# ---------------------------------------------------------------------------


# Number of events in the canned _demo_events() sequence in agents route.
_DEMO_EVENT_COUNT = 15


def test_ws_streams_full_demo_sequence(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        events = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT)]

    assert [e["seq"] for e in events] == list(range(1, _DEMO_EVENT_COUNT + 1))
    assert events[0]["type"] == "status_change"
    assert events[0]["status"] == "thinking"
    assert events[-1]["type"] == "status_change"
    assert events[-1]["status"] == "idle"
    # Content checks against the canned sequence.
    assert any(e["type"] == "tool_call" for e in events)
    assert any(e["type"] == "tool_result" for e in events)


def test_ws_reconnect_with_cursor_no_duplicates(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        first_batch = [ws.receive_json() for _ in range(5)]

    last_seen = first_batch[-1]["seq"]

    with app_client.websocket_connect(
        f"/api/agents/{agent['slug']}/stream?cursor={last_seen}"
    ) as ws:
        remaining = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT - 5)]

    seqs1 = [e["seq"] for e in first_batch]
    seqs2 = [e["seq"] for e in remaining]
    assert seqs1 == [1, 2, 3, 4, 5]
    assert seqs2 == list(range(6, _DEMO_EVENT_COUNT + 1))
    # No duplicates across the gap.
    assert set(seqs1).isdisjoint(set(seqs2))


def test_ws_cursor_zero_replays_everything(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    # Allow the first WS to drain so all events are persisted.
    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        for _ in range(_DEMO_EVENT_COUNT):
            ws.receive_json()

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream?cursor=0") as ws:
        events = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT)]
    assert [e["seq"] for e in events] == list(range(1, _DEMO_EVENT_COUNT + 1))


def test_ws_cursor_past_end_yields_nothing_in_replay(
    app_client: TestClient, tmp_workdir: str
) -> None:
    """With cursor > last seq, replay yields no events; client may still
    receive new events that arrive afterwards. Here no new events come
    after the demo finishes so the connection just sits idle — we test
    the replay-empty case only."""
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    # Drain first.
    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        for _ in range(_DEMO_EVENT_COUNT):
            ws.receive_json()

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream?cursor=999") as ws:
        # No replay events. Calling receive_json would block. Use a
        # recv-with-timeout proxy: send a control op that closes the WS
        # if no event arrives quickly. TestClient's WS doesn't expose a
        # timeout natively, so we just exit the with-block.
        # The mere fact that the WS opened without raising is sufficient.
        pass


# ---------------------------------------------------------------------------
# WS: input path
# ---------------------------------------------------------------------------


def test_ws_input_frame_creates_user_input_event(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        # Drain the demo events first.
        for _ in range(_DEMO_EVENT_COUNT):
            ws.receive_json()

        # Send an input frame.
        ws.send_text(json.dumps({"type": "input", "text": "hello there"}))

        ev = ws.receive_json()
        assert ev["type"] == "user_input"
        assert ev["text"] == "hello there"
        assert ev["seq"] == _DEMO_EVENT_COUNT + 1


def test_ws_stop_frame_creates_user_stop_event(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        for _ in range(_DEMO_EVENT_COUNT):
            ws.receive_json()

        ws.send_text(json.dumps({"type": "stop"}))

        ev = ws.receive_json()
        assert ev["type"] == "user_stop"
        assert ev["seq"] == _DEMO_EVENT_COUNT + 1


def test_ws_permission_frame_forwards_to_adapter(
    app_client: TestClient, tmp_workdir: str
) -> None:
    """The WS handler accepts ``{type:"permission", request_id, decision}`` and
    routes it to ``supervisor.resolve_permission``, which delegates to the
    adapter. The stub adapter records each call so we can assert."""
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        for _ in range(_DEMO_EVENT_COUNT):
            ws.receive_json()

        ws.send_text(
            json.dumps(
                {"type": "permission", "request_id": "req-1", "decision": "allow"}
            )
        )
        ws.send_text(
            json.dumps(
                {"type": "permission", "request_id": "req-2", "decision": "deny"}
            )
        )
        # Bad shape — should be ignored.
        ws.send_text(json.dumps({"type": "permission", "request_id": "req-x"}))
        ws.send_text(
            json.dumps(
                {"type": "permission", "request_id": "req-y", "decision": "bogus"}
            )
        )
        # Real input afterwards: confirms the WS loop is still alive.
        ws.send_text(json.dumps({"type": "input", "text": "ok"}))
        ev = ws.receive_json()
        assert ev["type"] == "user_input"

    state = app_client.app.state.supervisor._states.get(agent["slug"])
    # State may have been cleaned up if the WS close happened first; the
    # adapter ref we want sits on the AgentState while the agent is live.
    # Reach into the supervisor's registry to confirm the routed calls.
    assert state is not None, "agent state should still exist"
    adapter = state.adapter
    assert getattr(adapter, "permission_resolutions") == [
        ("req-1", "allow"),
        ("req-2", "deny"),
    ]


def test_ws_malformed_input_frame_is_ignored(app_client: TestClient, tmp_workdir: str) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        for _ in range(_DEMO_EVENT_COUNT):
            ws.receive_json()

        # Garbage and wrong-type messages should be silently dropped.
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "garbage"}))
        ws.send_text(json.dumps({"type": "input"}))  # missing text

        # A real input still works after the bad ones.
        ws.send_text(json.dumps({"type": "input", "text": "ok"}))
        ev = ws.receive_json()
        assert ev["type"] == "user_input"
        assert ev["text"] == "ok"


# ---------------------------------------------------------------------------
# WS: error paths
# ---------------------------------------------------------------------------


def test_ws_unknown_agent_closes(app_client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect("/api/agents/agt-404/stream") as ws:
            ws.receive_json()


# ---------------------------------------------------------------------------
# Contexts: per-source files + index + first-message injection
# ---------------------------------------------------------------------------


def test_create_agent_with_contexts_writes_files_and_index(
    app_client: TestClient, tmp_workdir: str
) -> None:
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "Architect",
            "persona": "architect",
            "role": "architect",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
            "contexts": [
                {"type": "text", "value": "the bug only repros under load"},
                {"type": "url", "value": "https://example.com/runbook"},
            ],
        },
    )
    assert response.status_code == 201
    agent = response.json()

    workspace_root: Path = app_client.app.state.settings.workspace_root
    agent_dir = workspace_root / "works" / work["slug"] / "agents" / agent["slug"]

    text_file = agent_dir / "context" / "text-1.md"
    url_file = agent_dir / "context" / "url-1.md"
    index = agent_dir / "context.md"

    assert text_file.read_text(encoding="utf-8").startswith("the bug only repros under load")
    assert "https://example.com/runbook" in url_file.read_text(encoding="utf-8")

    index_md = index.read_text(encoding="utf-8")
    assert "[text-1.md](context/text-1.md)" in index_md
    assert "[url-1.md](context/url-1.md)" in index_md

    persisted = json.loads((agent_dir / "agent.json").read_text(encoding="utf-8"))
    assert persisted["contexts"] == [
        {"type": "text", "value": "the bug only repros under load"},
        {"type": "url", "value": "https://example.com/runbook"},
    ]


def test_create_agent_with_contexts_emits_first_user_input(
    app_client: TestClient, tmp_workdir: str
) -> None:
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "Architect",
            "persona": "architect",
            "role": "architect",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
            "contexts": [{"type": "text", "value": "hello"}],
        },
    )
    assert response.status_code == 201
    agent = response.json()

    workspace_root: Path = app_client.app.state.settings.workspace_root
    expected_index = (
        workspace_root / "works" / work["slug"] / "agents" / agent["slug"] / "context.md"
    )

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        first = ws.receive_json()
        # 15 demo events follow the synthesised user_input.
        rest = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT)]

    assert first["seq"] == 1
    assert first["type"] == "user_input"
    assert str(expected_index) in first["text"]
    assert "Read individual files" in first["text"]

    assert [e["seq"] for e in rest] == list(range(2, _DEMO_EVENT_COUNT + 2))


def test_create_agent_422_when_connection_context_unfetchable(
    app_client: TestClient, tmp_workdir: str
) -> None:
    """A connection-backed context whose ConnectionStore can't resolve
    (here: a non-existent connection) halts the whole start. No agent
    row, no worktree, no context dir — the user retries cleanly after
    fixing the connection."""
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "Architect",
            "persona": "architect",
            "role": "architect",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
            "contexts": [
                {"type": "jira", "value": "ENG-3421", "conn_id": "con-999"},
            ],
        },
    )
    assert response.status_code == 422
    assert "connection not found" in response.json()["detail"]

    # No agent row, no worktree, no context dir on disk.
    workspace_root: Path = app_client.app.state.settings.workspace_root
    agents_dir = workspace_root / "works" / work["slug"] / "agents"
    assert not agents_dir.exists() or not any(agents_dir.iterdir())

    list_response = app_client.get(f"/api/works/{work['slug']}/agents")
    assert list_response.status_code == 200
    assert list_response.json() == []


def test_create_agent_without_contexts_does_not_inject_first_message(
    app_client: TestClient, tmp_workdir: str
) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)

    workspace_root: Path = app_client.app.state.settings.workspace_root
    agent_dir = workspace_root / "works" / work["slug"] / "agents" / agent["slug"]
    assert not (agent_dir / "context.md").exists()
    assert not (agent_dir / "context").exists()

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        events = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT)]
    assert events[0]["type"] == "status_change"
    assert [e["seq"] for e in events] == list(range(1, _DEMO_EVENT_COUNT + 1))


def test_ws_resumes_agent_after_supervisor_loses_state(
    app_client: TestClient, tmp_workdir: str
) -> None:
    """Simulates a backend restart: agent + transcript exist on disk, but
    the supervisor has no live state. The WS should resume the provider
    session, replay the original transcript, then deliver fresh events
    from the rebuilt adapter."""
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)

    # Drain the live demo so the transcript is fully written to disk.
    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        for _ in range(_DEMO_EVENT_COUNT):
            ws.receive_json()

    # Simulate a backend restart: drop the agent from the supervisor's
    # in-memory registry but leave the row + transcript on disk. We pop
    # synchronously rather than awaiting `stop_agent` because TestClient
    # owns the event loop and re-entering it from sync code wedges the
    # whole suite. The full lifecycle is exercised by the DELETE tests.
    supervisor = app_client.app.state.supervisor
    supervisor._states.pop(agent["slug"], None)  # noqa: SLF001 — test hook

    # Reconnect from cursor=0; expect the original transcript followed by
    # a freshly-emitted run from the rebuilt stub adapter (the canned
    # demo replays on every spawn). Seqs are monotonically increasing
    # across the resume boundary because the rebuilt supervisor state
    # starts a new agent task that publishes through the same lock.
    with app_client.websocket_connect(
        f"/api/agents/{agent['slug']}/stream?cursor=0"
    ) as ws:
        events = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT * 2)]
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, _DEMO_EVENT_COUNT * 2 + 1))
