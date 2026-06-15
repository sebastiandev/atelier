import subprocess

import pytest

from src.infrastructure.agents import opencode_models


def test_list_opencode_models_parses_plain_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[0] == ["opencode", "models", "--refresh"]
        return subprocess.CompletedProcess(
            args=["opencode", "models"],
            returncode=0,
            stdout=(
                "opencode/big-pickle\n"
                "openai/gpt-5.5\n"
                "\x1b[91mnot-a-model\x1b[0m\n"
                "openai/gpt-5.5\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(opencode_models.subprocess, "run", fake_run)

    models = opencode_models.list_opencode_models(refresh=True)

    assert [model.value for model in models] == [
        "opencode/big-pickle",
        "openai/gpt-5.5",
    ]
    assert models[1].label == "OpenAI / GPT 5.5"


def test_list_opencode_models_raises_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["opencode", "models"],
            returncode=1,
            stdout="",
            stderr="\x1b[91mError: no auth\x1b[0m",
        )

    monkeypatch.setattr(opencode_models.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Error: no auth"):
        opencode_models.list_opencode_models()
