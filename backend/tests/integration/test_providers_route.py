"""Integration test for GET /api/providers."""

from fastapi.testclient import TestClient


def test_lists_registered_providers(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    assert response.status_code == 200
    body = response.json()
    names = [p["name"] for p in body]
    assert names == ["claude-code", "amp"]


def test_claude_descriptor_includes_thinking_effort(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    claude = next(p for p in response.json() if p["name"] == "claude-code")
    assert claude["primary_field"]["label"] == "Model"
    assert "thinking_effort" in claude["options"]
    assert claude["options"]["thinking_effort"]["default"] == "off"


def test_amp_descriptor_has_mode_selector(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    amp = next(p for p in response.json() if p["name"] == "amp")
    assert amp["primary_field"]["label"] == "Mode"
    assert "smart" in amp["primary_field"]["values"]
    assert amp["options"] == {}
