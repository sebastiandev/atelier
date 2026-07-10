"""Integration test for GET /api/providers."""

from fastapi.testclient import TestClient

from src.infrastructure.agents.opencode_models import OpenCodeModelOption


def test_lists_new_session_providers(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    assert response.status_code == 200
    body = response.json()
    names = [p["name"] for p in body]
    assert names == ["claude-acp", "amp", "codex-acp", "opencode"]


def test_acp_descriptors_are_wire_complete(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    by_name = {p["name"]: p for p in response.json()}
    claude_acp = by_name["claude-acp"]
    assert claude_acp["label"] == "Claude Code (Anthropic)"
    assert claude_acp["primary_field"]["default"] == "default"
    assert "permission_mode" in claude_acp["options"]
    codex_acp = by_name["codex-acp"]
    assert codex_acp["label"] == "Codex (OpenAI)"
    assert "mode" in codex_acp["options"]
    assert "reasoning_effort" in codex_acp["options"]
    assert "fast-mode" in codex_acp["options"]
    opencode = by_name["opencode"]
    assert opencode["label"] == "OpenCode"
    assert opencode["primary_field"]["values"] == ["configured-default"]
    assert "mode" in opencode["options"]


def test_claude_descriptor_includes_thinking_effort(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    claude = next(p for p in response.json() if p["name"] == "claude-acp")
    assert claude["primary_field"]["label"] == "Model"
    assert "thinking_effort" in claude["options"]
    assert claude["options"]["thinking_effort"]["default"] == "default"
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
    claude = next(p for p in response.json() if p["name"] == "claude-acp")
    meta = claude["model_meta"]
    assert set(meta.keys()) == {
        "default",
        "claude-fable-5[1m]",
        "sonnet",
        "sonnet[1m]",
        "haiku",
    }
    assert claude["primary_field"]["default"] == "default"
    fable = meta["claude-fable-5[1m]"]
    assert fable["context_window"] == 1_000_000
    assert fable["input_per_mtok"] == 15.0
    assert fable["output_per_mtok"] == 75.0
    assert fable["cache_read_per_mtok"] == 1.5
    assert fable["cache_write_per_mtok"] == 18.75
    assert fable["effort_values"] is None
    assert fable["effort_default"] == "xhigh"
    default = meta["default"]
    assert default["context_window"] == 1_000_000
    assert default["input_per_mtok"] == 5.0
    assert default["output_per_mtok"] == 25.0
    sonnet = meta["sonnet"]
    assert sonnet["context_window"] == 200_000
    assert sonnet["input_per_mtok"] == 3.0
    assert sonnet["output_per_mtok"] == 15.0
    sonnet_1m = meta["sonnet[1m]"]
    assert sonnet_1m["context_window"] == 1_000_000


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


def test_codex_descriptor_has_acp_mode_and_reasoning(app_client: TestClient) -> None:
    response = app_client.get("/api/providers")
    codex = next(p for p in response.json() if p["name"] == "codex-acp")
    assert codex["primary_field"]["label"] == "Model"
    assert codex["primary_field"]["default"] == "gpt-5.5"
    assert "gpt-5.6-terra" in codex["primary_field"]["values"]
    assert "gpt-5.6-luna" in codex["primary_field"]["values"]
    assert "gpt-5.6-sol" in codex["primary_field"]["values"]
    assert "gpt-5.4" in codex["primary_field"]["values"]
    assert "gpt-5.5" in codex["primary_field"]["values"]
    assert "gpt-5.4-mini" in codex["primary_field"]["values"]
    assert "mode" in codex["options"]
    assert "reasoning_effort" in codex["options"]
    assert codex["options"]["reasoning_effort"]["values"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "extra",
        "ultra",
    ]
    assert codex["options"]["reasoning_effort"]["default"] == "medium"
    assert codex["options"]["mode"]["default"] == "auto"
    assert codex["options"]["fast-mode"]["values"] == ["off", "on"]
    assert codex["options"]["fast-mode"]["default"] == "off"
    assert "Mode" in codex["advanced_intro"]
    assert codex["model_meta"]["gpt-5.6-terra"]["input_per_mtok"] is None
    assert codex["model_meta"]["gpt-5.6-luna"]["input_per_mtok"] is None
    assert codex["model_meta"]["gpt-5.6-sol"]["input_per_mtok"] is None
    assert codex["model_meta"]["gpt-5.5"]["context_window"] == 400_000
    assert codex["model_meta"]["gpt-5.5"]["input_per_mtok"] == 5.0
    assert codex["model_meta"]["gpt-5.5"]["output_per_mtok"] == 30.0
    assert codex["model_meta"]["gpt-5.4"]["context_window"] == 272_000
    assert codex["model_meta"]["gpt-5.4-mini"]["input_per_mtok"] is None


def test_opencode_models_endpoint_lists_cli_models(
    app_client: TestClient, monkeypatch
) -> None:
    def fake_list(*, refresh: bool = False):
        assert refresh is True
        return [
            OpenCodeModelOption(value="openai/gpt-5.5", label="OpenAI / GPT 5.5")
        ]

    monkeypatch.setattr(
        "src.application.http.routes.providers.list_opencode_models",
        fake_list,
    )

    response = app_client.get("/api/providers/opencode/models?refresh=true")

    assert response.status_code == 200
    assert response.json() == [
        {"value": "openai/gpt-5.5", "label": "OpenAI / GPT 5.5"}
    ]


def test_opencode_models_endpoint_returns_503_on_cli_failure(
    app_client: TestClient, monkeypatch
) -> None:
    def fake_list(*, refresh: bool = False):
        raise RuntimeError("opencode CLI is not installed")

    monkeypatch.setattr(
        "src.application.http.routes.providers.list_opencode_models",
        fake_list,
    )

    response = app_client.get("/api/providers/opencode/models")

    assert response.status_code == 503
    assert response.json()["detail"] == "opencode CLI is not installed"
