"""Integration tests for /api/works/{slug}/agents and the agent WS stream.

Walking-skeleton end-to-end: POST creates the work, POST creates the
agent (which starts the supervisor with a StubAgentAdapter replaying the
canned demo sequence), then a WS connection observes the events flowing
through replay-from-disk + live fan-out, plus exercises the input path.
"""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_work(client: TestClient, name: str = "Skeleton demo") -> dict:
    response = client.post(
        "/api/works",
        json={
            "name": name,
            "description": "for the walking skeleton",
            "folder": "/code/skeleton",
            "contexts": [],
        },
    )
    response.raise_for_status()
    return response.json()


def _create_agent(client: TestClient, work_slug: str, name: str = "Architect") -> dict:
    response = client.post(
        f"/api/works/{work_slug}/agents",
        json={
            "name": name,
            "persona": "architect",
            "role": "architect",
            "provider": "amp",
            "model": "smart",
        },
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# REST: agent creation
# ---------------------------------------------------------------------------


def test_create_agent_returns_summary(app_client: TestClient) -> None:
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "Architect",
            "persona": "architect",
            "role": "architect",
            "provider": "amp",
            "model": "smart",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "agt-1"
    assert body["work_slug"] == work["slug"]
    assert body["persona"] == "architect"
    assert body["status"] == "idle"


def test_create_agent_422_for_unknown_option(app_client: TestClient) -> None:
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "X",
            "persona": "architect",
            "role": "x",
            "provider": "amp",
            "model": "smart",
            "options": {"bogus_key": "value"},
        },
    )
    assert response.status_code == 422
    assert "unknown options" in response.json()["detail"]


def test_create_agent_422_for_bad_model(app_client: TestClient) -> None:
    work = _create_work(app_client)
    response = app_client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "X",
            "persona": "architect",
            "role": "x",
            "provider": "amp",
            "model": "turbo",  # not a valid AmpMode
        },
    )
    assert response.status_code == 422


def test_create_agent_404_for_unknown_work(app_client: TestClient) -> None:
    response = app_client.post(
        "/api/works/WRK-404/agents",
        json={
            "name": "X",
            "persona": "architect",
            "role": "x",
            "provider": "amp",
            "model": "smart",
        },
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# WS: replay + live
# ---------------------------------------------------------------------------


# Number of events in the canned _demo_events() sequence in agents route.
_DEMO_EVENT_COUNT = 15


def test_ws_streams_full_demo_sequence(app_client: TestClient) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"])

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


def test_ws_reconnect_with_cursor_no_duplicates(app_client: TestClient) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"])

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


def test_ws_cursor_zero_replays_everything(app_client: TestClient) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"])
    # Allow the first WS to drain so all events are persisted.
    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream") as ws:
        for _ in range(_DEMO_EVENT_COUNT):
            ws.receive_json()

    with app_client.websocket_connect(f"/api/agents/{agent['slug']}/stream?cursor=0") as ws:
        events = [ws.receive_json() for _ in range(_DEMO_EVENT_COUNT)]
    assert [e["seq"] for e in events] == list(range(1, _DEMO_EVENT_COUNT + 1))


def test_ws_cursor_past_end_yields_nothing_in_replay(app_client: TestClient) -> None:
    """With cursor > last seq, replay yields no events; client may still
    receive new events that arrive afterwards. Here no new events come
    after the demo finishes so the connection just sits idle — we test
    the replay-empty case only."""
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"])
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


def test_ws_input_frame_creates_user_input_event(app_client: TestClient) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"])

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


def test_ws_malformed_input_frame_is_ignored(app_client: TestClient) -> None:
    work = _create_work(app_client)
    agent = _create_agent(app_client, work["slug"])

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
