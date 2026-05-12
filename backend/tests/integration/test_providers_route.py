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
    # Dialog pre-selects the highest-but-one tier (matches Claude Code CLI's
    # default ``/effort`` setting). Build-time fallback when no value is sent
    # is still "off" — see test_claude_build_with_defaults.
    assert claude["options"]["thinking_effort"]["default"] == "xhigh"
    assert "xhigh" in claude["options"]["thinking_effort"]["values"]


def test_amp_descriptor_has_mode_selector(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    amp = next(p for p in response.json() if p["name"] == "amp")
    assert amp["primary_field"]["label"] == "Mode"
    assert "smart" in amp["primary_field"]["values"]
    assert "permission_mode" in amp["options"]
    assert amp["options"]["permission_mode"]["default"] == "default"
    assert "custom_allowed_tools" in amp["text_options"]
    assert amp["text_options"]["custom_allowed_tools"]["visible_when"] == [
        "permission_mode",
        "custom",
    ]
    assert "Permissions decide" in amp["advanced_intro"]


def test_claude_descriptor_exposes_model_meta(app_client: TestClient) -> None:
    """FE reads pricing + context window from ``model_meta``; verify the
    wire format is exactly what AgentTile's TurnMetricsBar expects."""
    response = app_client.get("/api/providers")
    claude = next(p for p in response.json() if p["name"] == "claude-code")
    meta = claude["model_meta"]
    assert set(meta.keys()) == {
        "claude-opus-4-7[1m]",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    }
    opus = meta["claude-opus-4-7"]
    assert opus["context_window"] == 200_000
    assert opus["input_per_mtok"] == 15.0
    assert opus["output_per_mtok"] == 75.0
    assert opus["cache_read_per_mtok"] == 1.5
    assert opus["cache_write_per_mtok"] == 18.75
    opus_1m = meta["claude-opus-4-7[1m]"]
    assert opus_1m["context_window"] == 1_000_000
    # 1M variant uses the same flat per-token rates today; the surcharge
    # above 200k input tokens isn't modeled. See `_CLAUDE_MODEL_META`.
    assert opus_1m["input_per_mtok"] == 15.0
    assert opus_1m["output_per_mtok"] == 75.0


def test_amp_descriptor_exposes_per_mode_context_window(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    amp = next(p for p in response.json() if p["name"] == "amp")
    meta = amp["model_meta"]
    assert set(meta.keys()) == {"smart", "rush", "deep", "large"}
    assert meta["smart"]["context_window"] == 200_000
    assert meta["rush"]["context_window"] == 200_000
    assert meta["deep"]["context_window"] == 1_000_000
    assert meta["large"]["context_window"] == 1_000_000
    # Pricing isn't published per mode — kept null so the FE shows "—".
    assert meta["smart"]["input_per_mtok"] is None
    assert meta["smart"]["output_per_mtok"] is None
