import subprocess

import pytest

from src.infrastructure.agents import opencode_models

_VERBOSE_STDOUT = """opencode/big-pickle
{
  "id": "big-pickle",
  "name": "Big Pickle",
  "api": { "npm": "@ai-sdk/anthropic" },
  "limit": { "context": 200000 }
}
openai/gpt-5.5
{
  "id": "gpt-5.5",
  "variants": {
    "low": { "reasoningEffort": "low" },
    "high": { "reasoningEffort": "high" }
  }
}
"""


def _stub_run(calls: list[list[str]], *, results: dict[bool, tuple[int, str]]):
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        argv = list(args[0])  # type: ignore[arg-type]
        calls.append(argv)
        returncode, stdout = results["--verbose" in argv]
        return subprocess.CompletedProcess(
            args=argv, returncode=returncode, stdout=stdout, stderr=""
        )

    return fake_run


def test_verbose_listing_carries_per_model_effort_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        opencode_models.subprocess,
        "run",
        _stub_run(calls, results={True: (0, _VERBOSE_STDOUT)}),
    )

    models = opencode_models.list_opencode_models(refresh=True)

    assert calls == [["opencode", "models", "--verbose", "--refresh"]]
    assert [(model.value, model.effort_values) for model in models] == [
        ("opencode/big-pickle", ()),
        ("openai/gpt-5.5", ("low", "high")),
    ]


def test_plain_listing_is_parsed_and_reports_no_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = (
        "opencode/big-pickle\n"
        "openai/gpt-5.5\n"
        "\x1b[91mnot-a-model\x1b[0m\n"
        "openai/gpt-5.5\n"
    )
    monkeypatch.setattr(
        opencode_models.subprocess,
        "run",
        _stub_run([], results={True: (0, stdout)}),
    )

    models = opencode_models.list_opencode_models()

    assert [model.value for model in models] == [
        "opencode/big-pickle",
        "openai/gpt-5.5",
    ]
    assert models[1].label == "OpenAI / GPT 5.5"
    assert all(model.effort_values == () for model in models)


def test_cli_without_verbose_flag_falls_back_to_the_plain_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        opencode_models.subprocess,
        "run",
        _stub_run(
            calls,
            results={True: (1, ""), False: (0, "openai/gpt-5.5\n")},
        ),
    )

    models = opencode_models.list_opencode_models()

    assert calls == [
        ["opencode", "models", "--verbose"],
        ["opencode", "models"],
    ]
    assert [model.value for model in models] == ["openai/gpt-5.5"]


def test_list_opencode_models_raises_clean_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(args[0]),  # type: ignore[arg-type]
            returncode=1,
            stdout="",
            stderr="\x1b[91mError: no auth\x1b[0m",
        )

    monkeypatch.setattr(opencode_models.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Error: no auth"):
        opencode_models.list_opencode_models()
