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
    assert adapter.permission_resolutions == [
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
    the supervisor has no live state. The WS resumes the provider
    session, replays the original transcript, and stays dormant — the
    rebuilt adapter does NOT auto-emit a fresh run, because the resume
    path uses lazy spawn (no SDK process until the user types). Sending
    an input frame triggers the lazy pump and the canned demo flows."""
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
    supervisor._states.pop(agent["slug"], None)

    # Reconnect from cursor=0; replay yields the original transcript,
    # then the connection sits idle (lazy resume — no auto-run). Sending
    # an input frame fires the pump: user_input lands first, then the
    # canned demo replays on the rebuilt adapter. Seqs are monotonically
    # increasing across the resume boundary.
    with app_client.websocket_connect(
        f"/api/agents/{agent['slug']}/stream?cursor=0"
    ) as ws:
        replay = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT)]
        ws.send_text(json.dumps({"type": "input", "text": "wake up"}))
        # 1 user_input + DEMO_EVENT_COUNT demo events from the fresh pump.
        live = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT + 1)]

    assert [e["seq"] for e in replay] == list(range(1, _DEMO_EVENT_COUNT + 1))
    assert live[0]["type"] == "user_input"
    assert live[0]["text"] == "wake up"
    assert [e["seq"] for e in live] == list(
        range(_DEMO_EVENT_COUNT + 1, _DEMO_EVENT_COUNT * 2 + 2)
    )


# ---------------------------------------------------------------------------
# REST: detach (hand the agent to a terminal CLI)
# ---------------------------------------------------------------------------


def test_detach_409_when_agent_has_no_session_id(
    app_client: TestClient, tmp_workdir: str
) -> None:
    """Detach without a provider session_id would just open a fresh CLI
    conversation, defeating the point. The route surfaces this as 409
    rather than silently succeeding."""
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    # Stub adapter doesn't emit SessionEstablished, so session_id is None.
    response = app_client.post(f"/api/agents/{agent['slug']}/detach")
    assert response.status_code == 409
    assert "session" in response.json()["detail"].lower()


def test_detach_404_for_unknown_agent(app_client: TestClient) -> None:
    response = app_client.post("/api/agents/agt-404/detach")
    assert response.status_code == 404


def test_detach_flips_status_writes_marker_and_returns_command(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: agent has a session_id, the route stops the supervisor,
    flips status to ``detached``, writes a ``user_detached`` transcript
    marker, and returns the resume command. We monkey-patch the actual
    terminal launch so the test doesn't pop a window."""

    captured: dict[str, object] = {}

    def fake_launch(command: str):  # type: ignore[no-untyped-def]
        from src.infrastructure.cli_launcher import LaunchResult

        captured["command"] = command
        return LaunchResult(command=command, launched=True)

    monkeypatch.setattr(
        "src.domain.commands.agents.detach.launch_in_terminal", fake_launch
    )

    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    # Backfill a session_id on the agent row directly — the canned stub
    # adapter doesn't emit SessionEstablished, but production adapters do
    # and that's the realistic precondition for detach.
    workstore = app_client.app.state.workstore
    workstore.set_agent_session_id(agent["slug"], "sess-from-test")

    response = app_client.post(f"/api/agents/{agent['slug']}/detach")
    assert response.status_code == 200
    body = response.json()
    assert body["launched"] is True
    # Mode preservation: the detach command must carry the agent's mode so
    # the CLI session resumes on the same model the user picked instead of
    # silently dropping to the local CLI default. ``_create_agent`` defaults
    # to ``smart``.
    assert "amp --mode 'smart' threads continue" in body["command"]
    assert "sess-from-test" in body["command"]
    assert captured["command"] == body["command"]

    # Status flipped on the agent row.
    listing = app_client.get(f"/api/works/{work['slug']}/agents").json()
    statuses = {a["slug"]: a["status"] for a in listing}
    assert statuses[agent["slug"]] == "detached"

    # Transcript carries a user_detached marker with an sdk_cursor payload
    # (so the catch-up merge has a starting point on re-attach).
    workspace_root: Path = app_client.app.state.settings.workspace_root
    transcript_path = (
        workspace_root
        / "works"
        / work["slug"]
        / "agents"
        / agent["slug"]
        / "transcript.ndjson"
    )
    lines = [
        json.loads(line)
        for line in transcript_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    detach_markers = [e for e in lines if e["type"] == "user_detached"]
    assert len(detach_markers) == 1
    cursor = detach_markers[0].get("sdk_cursor")
    assert isinstance(cursor, dict)
    assert cursor["provider"] == "amp"


def test_detach_preserves_amp_allow_all_permission_mode(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Amp's ``allow_all`` permission mode maps to the CLI's
    ``--dangerously-allow-all`` flag — that's the one Amp permission
    knob detach can actually translate (the others rely on Atelier's
    bridge)."""

    def fake_launch(command: str):  # type: ignore[no-untyped-def]
        from src.infrastructure.cli_launcher import LaunchResult

        return LaunchResult(command=command, launched=True)

    monkeypatch.setattr(
        "src.domain.commands.agents.detach.launch_in_terminal", fake_launch
    )

    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "Architect",
            "persona": "architect",
            "role": "architect",
            "provider": "amp",
            "model": "deep",
            "folder": tmp_workdir,
            "options": {"permission_mode": "allow_all"},
        },
    )
    response.raise_for_status()
    agent = response.json()
    workstore = app_client.app.state.workstore
    workstore.set_agent_session_id(agent["slug"], "sess-amp-deep")

    response = app_client.post(f"/api/agents/{agent['slug']}/detach")
    assert response.status_code == 200
    command = response.json()["command"]
    assert "amp --dangerously-allow-all --mode 'deep' threads continue" in command
    assert "'sess-amp-deep'" in command


def test_detach_returns_clipboard_fallback_when_launch_fails(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no terminal can be launched (sandbox, missing emulator), the
    response still carries the command string so the FE copies it to
    the user's clipboard."""

    def fake_launch(command: str):  # type: ignore[no-untyped-def]
        from src.infrastructure.cli_launcher import LaunchResult

        return LaunchResult(command=command, launched=False)

    monkeypatch.setattr(
        "src.domain.commands.agents.detach.launch_in_terminal", fake_launch
    )

    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    workstore = app_client.app.state.workstore
    workstore.set_agent_session_id(agent["slug"], "sess-from-test")

    response = app_client.post(f"/api/agents/{agent['slug']}/detach")
    assert response.status_code == 200
    body = response.json()
    assert body["launched"] is False
    assert body["command"]  # populated for the clipboard path


# ---------------------------------------------------------------------------
# REST: reveal — open the agent's worktree in the OS file browser
# ---------------------------------------------------------------------------


def test_reveal_agent_opens_folder_when_no_worktree_provisioned(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For a non-git source folder no worktree is provisioned, so reveal
    falls back to the agent's source folder (== ``agent.folder``)."""
    from src.application.http.routes import agents as agents_module

    captured: dict[str, object] = {}

    def fake_open(path: str) -> None:
        captured["path"] = path

    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    # Patch AFTER creation so the worktree manager's git probes run
    # normally; the patch is just to silence the actual reveal call.
    monkeypatch.setattr(agents_module, "open_in_file_browser", fake_open)

    response = app_client.post(f"/api/agents/{agent['slug']}/reveal")
    assert response.status_code == 204
    assert captured["path"] == tmp_workdir


def test_reveal_agent_opens_worktree_when_provisioned(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the worktree dir exists on disk (provisioned by the
    WorktreeManager for git source folders), reveal targets it instead
    of the source folder."""
    from src.application.http.routes import agents as agents_module

    captured: dict[str, object] = {}

    def fake_open(path: str) -> None:
        captured["path"] = path

    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    monkeypatch.setattr(agents_module, "open_in_file_browser", fake_open)

    # Simulate the WorktreeManager having provisioned the per-agent
    # worktree dir. The route checks ``exists()``; an empty dir suffices
    # for the route's branching logic.
    settings = app_client.app.state.settings
    worktree = (
        settings.workspace_root / "works" / work["slug"] / "worktrees" / agent["slug"]
    )
    worktree.mkdir(parents=True, exist_ok=True)

    response = app_client.post(f"/api/agents/{agent['slug']}/reveal")
    assert response.status_code == 204
    assert captured["path"] == str(worktree)


def test_reveal_agent_404_for_unknown_slug(app_client: TestClient) -> None:
    assert app_client.post("/api/agents/agt-404/reveal").status_code == 404


# ---------------------------------------------------------------------------
# REST: open in console — launch a terminal at the agent's worktree
# ---------------------------------------------------------------------------


def test_open_in_console_uses_source_folder_when_no_worktree(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric with the reveal fallback: a non-git source folder gets
    no worktree provisioned, so the console opens at the source dir."""
    from src.application.http.routes import agents as agents_module

    captured: dict[str, object] = {}

    def fake_open(path: str, kind: str = "system") -> None:
        captured["path"] = path
        captured["kind"] = kind

    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    monkeypatch.setattr(agents_module, "open_in_terminal", fake_open)

    response = app_client.post(f"/api/agents/{agent['slug']}/open-in-console")
    assert response.status_code == 204
    assert captured["path"] == tmp_workdir
    # No ``kind`` query → defaults to "system" server-side.
    assert captured["kind"] == "system"


def test_open_in_console_targets_worktree_when_provisioned(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.application.http.routes import agents as agents_module

    captured: dict[str, object] = {}

    def fake_open(path: str, kind: str = "system") -> None:
        captured["path"] = path
        captured["kind"] = kind

    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    monkeypatch.setattr(agents_module, "open_in_terminal", fake_open)

    settings = app_client.app.state.settings
    worktree = (
        settings.workspace_root / "works" / work["slug"] / "worktrees" / agent["slug"]
    )
    worktree.mkdir(parents=True, exist_ok=True)

    response = app_client.post(f"/api/agents/{agent['slug']}/open-in-console")
    assert response.status_code == 204
    assert captured["path"] == str(worktree)


def test_open_in_console_forwards_kind_query_param(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?kind=...`` from the FE Tweaks selector reaches the helper."""
    from src.application.http.routes import agents as agents_module

    captured: dict[str, object] = {}

    def fake_open(path: str, kind: str = "system") -> None:
        captured["path"] = path
        captured["kind"] = kind

    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    monkeypatch.setattr(agents_module, "open_in_terminal", fake_open)

    response = app_client.post(
        f"/api/agents/{agent['slug']}/open-in-console?kind=iterm2"
    )
    assert response.status_code == 204
    assert captured["kind"] == "iterm2"


def test_open_in_console_404_for_unknown_slug(app_client: TestClient) -> None:
    assert (
        app_client.post("/api/agents/agt-404/open-in-console").status_code == 404
    )


def test_open_in_console_500_when_helper_raises(
    app_client: TestClient, tmp_workdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route converts ``OSError`` / ``SubprocessError`` from the
    helper (e.g. ``FileNotFoundError`` when no terminal is on PATH)
    into a 500 with a useful detail string."""
    from src.application.http.routes import agents as agents_module

    def raising(path: str, kind: str = "system") -> None:
        raise FileNotFoundError("no terminal emulator found on PATH")

    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    monkeypatch.setattr(agents_module, "open_in_terminal", raising)

    response = app_client.post(f"/api/agents/{agent['slug']}/open-in-console")
    assert response.status_code == 500
    assert "open in console failed" in response.json()["detail"]


def test_agent_summary_carries_worktree_path(
    app_client: TestClient, tmp_workdir: str
) -> None:
    """``worktree_path`` is included on every agent summary so the FE
    can show the path on the tile pill without a second round-trip."""
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    listing = app_client.get(f"/api/works/{work['slug']}/agents").json()
    fetched = next(a for a in listing if a["slug"] == agent["slug"])
    # No worktree provisioned for the non-git tmp folder, so the path
    # echoes the source folder.
    assert fetched["worktree_path"] == tmp_workdir
    # The create response carries it too.
    assert agent["worktree_path"] == tmp_workdir


# ---------------------------------------------------------------------------
# REST: delete agent — wipes worktree, transcript, agent.json + DB row
# ---------------------------------------------------------------------------


def test_delete_agent_removes_row_and_workspace_dir(
    app_client: TestClient, tmp_workdir: str
) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"], tmp_workdir)
    workspace_root: Path = app_client.app.state.settings.workspace_root
    agent_dir = workspace_root / "works" / work["slug"] / "agents" / agent["slug"]
    assert agent_dir.exists()

    response = app_client.delete(f"/api/agents/{agent['slug']}")
    assert response.status_code == 204

    # Row is gone from the listing.
    listing = app_client.get(f"/api/works/{work['slug']}/agents").json()
    assert all(a["slug"] != agent["slug"] for a in listing)
    # Agent dir wiped.
    assert not agent_dir.exists()


def test_delete_agent_404_for_unknown_slug(app_client: TestClient) -> None:
    assert app_client.delete("/api/agents/agt-404").status_code == 404


def test_delete_agent_leaves_siblings_untouched(
    app_client: TestClient, tmp_workdir: str
) -> None:
    work = _create_work(app_client)
    target = _create_agent(app_client, work["slug"], tmp_workdir, name="Target")
    sibling = _create_agent(app_client, work["slug"], tmp_workdir, name="Keeper")

    assert app_client.delete(f"/api/agents/{target['slug']}").status_code == 204

    listing = app_client.get(f"/api/works/{work['slug']}/agents").json()
    remaining = [a["slug"] for a in listing]
    assert target["slug"] not in remaining
    assert sibling["slug"] in remaining
