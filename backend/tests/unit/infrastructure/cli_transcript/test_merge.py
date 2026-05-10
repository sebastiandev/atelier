"""Unit tests for the CLI transcript catch-up merge.

Builds in-memory fixtures matching the on-disk shape of Claude Code's
``~/.claude/projects/<munged>/<session>.jsonl`` and Amp's
``~/.local/share/amp/threads/<id>.json`` files, points the merge at
them, and asserts the translated AgentEvent dicts. Avoids touching the
real ``$HOME`` so the tests are hermetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.infrastructure.cli_transcript import (
    merge_cli_transcript,
    sdk_cursor_at_detach,
    sdk_transcript_path,
)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_claude_path_munges_cwd_with_dashes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = sdk_transcript_path(
        "claude-code", "abc-123", Path("/Users/seba/src/atelier")
    )
    assert path == tmp_path / ".claude" / "projects" / "-Users-seba-src-atelier" / "abc-123.jsonl"


def test_amp_returns_no_path_now_that_threads_are_server_side() -> None:
    # Modern Amp threads aren't on disk — we shell out to ``amp threads
    # export`` instead. ``sdk_transcript_path`` returns None for amp so
    # callers can branch cleanly.
    assert sdk_transcript_path("amp", "T-foo", Path("/x")) is None


def test_path_returns_none_for_unknown_provider() -> None:
    # ``Provider`` is a Literal so this isn't reachable from typed callers,
    # but the runtime guard exists for safety.
    assert sdk_transcript_path("honeycomb", "x", Path("/x")) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers — fake ``amp threads export`` subprocess
# ---------------------------------------------------------------------------


def _install_fake_amp(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    returncode: int = 0,
    raises: Exception | None = None,
) -> dict[str, Any]:
    """Replace ``subprocess.run`` inside the cli_transcript module with a
    handler that captures the args and returns a synthetic result. Tests
    that need different behaviours can pass different combinations."""
    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        if raises is not None:
            raise raises
        import subprocess

        return subprocess.CompletedProcess(
            argv, returncode, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(
        "src.infrastructure.cli_transcript.subprocess.run", fake_run
    )
    return captured


# ---------------------------------------------------------------------------
# Cursor snapshots
# ---------------------------------------------------------------------------


def test_claude_cursor_returns_anchor_ts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project_dir = tmp_path / ".claude" / "projects" / "-x"
    project_dir.mkdir(parents=True)
    (project_dir / "sess.jsonl").write_text("", encoding="utf-8")
    cursor = sdk_cursor_at_detach("claude-code", "sess", Path("/x"))
    assert cursor["provider"] == "claude-code"
    assert "anchor_ts" in cursor


def test_amp_cursor_shells_out_to_threads_export(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"messages": [{"role": "user"}, {"role": "assistant"}, {"role": "user"}]}
    captured = _install_fake_amp(monkeypatch, stdout=json.dumps(payload))
    cursor = sdk_cursor_at_detach("amp", "T-foo", Path("/x"))
    assert cursor == {"provider": "amp", "message_count": 3}
    # Round-trips through the CLI's documented invocation.
    assert captured["argv"] == ["amp", "threads", "export", "T-foo"]


def test_amp_cursor_returns_zero_when_export_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``amp`` not on PATH, network out, auth expired — all map to
    ``message_count=0`` so the merge later behaves as "everything is
    new" rather than crashing the re-attach."""
    _install_fake_amp(monkeypatch, raises=FileNotFoundError("amp"))
    cursor = sdk_cursor_at_detach("amp", "T-missing", Path("/x"))
    assert cursor == {"provider": "amp", "message_count": 0}


def test_amp_cursor_returns_zero_when_export_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_amp(monkeypatch, returncode=2, stdout="")
    cursor = sdk_cursor_at_detach("amp", "T-missing", Path("/x"))
    assert cursor == {"provider": "amp", "message_count": 0}


# ---------------------------------------------------------------------------
# Claude jsonl merge
# ---------------------------------------------------------------------------


def _claude_user_line(text: str, ts: str, **extra: Any) -> str:
    return json.dumps(
        {
            "type": "user",
            "timestamp": ts,
            "message": {"role": "user", "content": text},
            **extra,
        }
    )


def _claude_assistant_line(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    usage: dict[str, Any] | None = None,
    model: str | None = None,
) -> str:
    message: dict[str, Any] = {"role": "assistant", "content": blocks}
    if usage is not None:
        message["usage"] = usage
    if model is not None:
        message["model"] = model
    return json.dumps({"type": "assistant", "timestamp": ts, "message": message})


def _claude_housekeeping_line(ts: str) -> str:
    return json.dumps({"type": "permission-mode", "timestamp": ts, "permissionMode": "default"})


def _write_claude_session(
    home: Path, cwd: Path, session_id: str, lines: list[str]
) -> None:
    project = str(cwd).replace("/", "-")
    project_dir = home / ".claude" / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / f"{session_id}.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def test_claude_merge_emits_only_post_anchor_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cwd = Path("/Users/seba/code/x")
    _write_claude_session(
        tmp_path,
        cwd,
        "sess",
        [
            _claude_user_line("before", "2026-05-05T10:00:00Z"),
            _claude_assistant_line(
                [{"type": "text", "text": "before reply"}], "2026-05-05T10:00:01Z"
            ),
            _claude_user_line("after", "2026-05-05T11:00:00Z"),
            _claude_assistant_line(
                [{"type": "text", "text": "after reply"}], "2026-05-05T11:00:01Z"
            ),
        ],
    )

    events = merge_cli_transcript(
        "claude-code",
        "sess",
        cwd,
        {"provider": "claude-code", "anchor_ts": "2026-05-05T10:30:00Z"},
    )

    assert [e["type"] for e in events] == ["user_input", "message_complete"]
    assert events[0]["text"] == "after"
    assert events[1]["text"] == "after reply"


def test_claude_merge_skips_housekeeping_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Permission-mode / file-history-snapshot / attachment lines aren't
    conversation events — they shouldn't show up in the user's
    transcript."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cwd = Path("/Users/seba/code/x")
    _write_claude_session(
        tmp_path,
        cwd,
        "sess",
        [
            _claude_housekeeping_line("2026-05-05T11:00:00Z"),
            _claude_user_line("real", "2026-05-05T11:00:01Z"),
        ],
    )
    events = merge_cli_transcript(
        "claude-code",
        "sess",
        cwd,
        {"provider": "claude-code", "anchor_ts": "2026-05-05T10:00:00Z"},
    )
    assert len(events) == 1
    assert events[0]["type"] == "user_input"


def test_claude_merge_handles_assistant_thinking_and_tool_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cwd = Path("/x")
    _write_claude_session(
        tmp_path,
        cwd,
        "sess",
        [
            _claude_assistant_line(
                [
                    {"type": "thinking", "thinking": "let me check"},
                    {"type": "text", "text": "Here you go"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"path": "/etc/hosts"},
                    },
                ],
                "2026-05-05T11:00:01Z",
            ),
        ],
    )
    events = merge_cli_transcript(
        "claude-code",
        "sess",
        cwd,
        {"provider": "claude-code", "anchor_ts": "2026-05-05T10:00:00Z"},
    )
    types = [e["type"] for e in events]
    assert types == ["thinking_complete", "message_complete", "tool_call"]
    assert events[2]["tool_id"] == "toolu_1"
    assert events[2]["arguments"] == {"path": "/etc/hosts"}


def test_claude_merge_translates_tool_results_inside_user_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Anthropic's wire shape carries tool results as ``tool_result`` blocks
    inside a follow-up user message. The merge should peel those out as
    ``ToolResult`` events even though they live under role=user."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cwd = Path("/x")
    _write_claude_session(
        tmp_path,
        cwd,
        "sess",
        [
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-05-05T11:00:01Z",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": "127.0.0.1 localhost",
                            }
                        ],
                    },
                }
            )
        ],
    )
    events = merge_cli_transcript(
        "claude-code",
        "sess",
        cwd,
        {"provider": "claude-code", "anchor_ts": "2026-05-05T10:00:00Z"},
    )
    assert len(events) == 1
    assert events[0]["type"] == "tool_result"
    assert events[0]["tool_id"] == "toolu_1"
    assert "localhost" in events[0]["content"]


def test_claude_merge_with_missing_file_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    events = merge_cli_transcript(
        "claude-code",
        "missing",
        Path("/x"),
        {"provider": "claude-code", "anchor_ts": "2026-05-05T10:00:00Z"},
    )
    assert events == []


# ---------------------------------------------------------------------------
# Amp merge — runs against ``amp threads export`` (server-side threads)
# ---------------------------------------------------------------------------


def _amp_export_payload(thread_id: str, messages: list[dict[str, Any]]) -> str:
    return json.dumps({"v": 1, "id": thread_id, "messages": messages})


def test_amp_merge_returns_messages_past_count(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _amp_export_payload(
        "T-foo",
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "before"}],
                "meta": {"sentAt": 1700000000000},
            },
            {"role": "assistant", "content": [{"type": "text", "text": "before reply"}]},
            {
                "role": "user",
                "content": [{"type": "text", "text": "after"}],
                "meta": {"sentAt": 1700000060000},
            },
            {"role": "assistant", "content": [{"type": "text", "text": "after reply"}]},
        ],
    )
    _install_fake_amp(monkeypatch, stdout=payload)
    events = merge_cli_transcript(
        "amp", "T-foo", Path("/x"), {"provider": "amp", "message_count": 2}
    )
    assert [e["type"] for e in events] == ["user_input", "message_complete"]
    assert events[0]["text"] == "after"


def test_amp_merge_handles_thinking_and_tool_use_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _amp_export_payload(
        "T-foo",
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "looking..."},
                    {"type": "text", "text": "found it"},
                    {
                        "type": "tool_use",
                        "id": "toolu_x",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                ],
            }
        ],
    )
    _install_fake_amp(monkeypatch, stdout=payload)
    events = merge_cli_transcript(
        "amp", "T-foo", Path("/x"), {"provider": "amp", "message_count": 0}
    )
    types = [e["type"] for e in events]
    assert types == ["thinking_complete", "message_complete", "tool_call"]
    assert events[0]["text"] == "looking..."
    assert events[2]["name"] == "Bash"


def test_amp_merge_returns_empty_when_export_returns_garbage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_amp(monkeypatch, stdout="not json {")
    events = merge_cli_transcript(
        "amp", "T-bad", Path("/x"), {"provider": "amp", "message_count": 0}
    )
    assert events == []


def test_amp_merge_returns_empty_when_export_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the amp CLI isn't on PATH or fails for any reason, the merge
    silently produces zero events rather than failing the re-attach."""
    _install_fake_amp(monkeypatch, raises=FileNotFoundError("amp"))
    events = merge_cli_transcript(
        "amp", "T-foo", Path("/x"), {"provider": "amp", "message_count": 0}
    )
    assert events == []


def test_amp_merge_translates_tool_results_with_amp_camelcase_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Amp's ``tool_result`` block uses ``toolUseID`` (camelCase) and
    nests content under ``run.result.content`` instead of the Anthropic
    standard ``tool_use_id`` + ``content``. The translator handles both
    so the user's transcript carries actual tool output, not blanks."""
    payload = _amp_export_payload(
        "T-foo",
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "toolUseID": "toolu_abc",
                        "run": {
                            "status": "success",
                            "result": {"content": "1: hello\n2: world"},
                        },
                    }
                ],
            }
        ],
    )
    _install_fake_amp(monkeypatch, stdout=payload)
    events = merge_cli_transcript(
        "amp", "T-foo", Path("/x"), {"provider": "amp", "message_count": 0}
    )
    assert len(events) == 1
    assert events[0]["type"] == "tool_result"
    assert events[0]["tool_id"] == "toolu_abc"
    assert "hello" in events[0]["content"]
    assert events[0]["is_error"] is False


def test_amp_merge_marks_tool_result_as_error_when_status_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _amp_export_payload(
        "T-foo",
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "toolUseID": "toolu_abc",
                        "run": {
                            "status": "error",
                            "result": {"content": "command not found"},
                        },
                    }
                ],
            }
        ],
    )
    _install_fake_amp(monkeypatch, stdout=payload)
    events = merge_cli_transcript(
        "amp", "T-foo", Path("/x"), {"provider": "amp", "message_count": 0}
    )
    assert events[0]["is_error"] is True


def test_amp_merge_with_count_at_end_yields_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor count == len(messages) means the user detached and didn't
    type anything in CLI before re-attaching. Merge should be a no-op."""
    payload = _amp_export_payload(
        "T-foo",
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ],
    )
    _install_fake_amp(monkeypatch, stdout=payload)
    events = merge_cli_transcript(
        "amp", "T-foo", Path("/x"), {"provider": "amp", "message_count": 2}
    )
    assert events == []


# ---------------------------------------------------------------------------
# Usage → turn_metrics translation
#
# CLI-side turns bypass our adapters, so the live ``turn_metrics`` emit
# pathway never fires for them. The merge must reconstruct a turn_metrics
# event from each assistant message's ``usage`` so the FE's session cost
# + context % counters cover the detached period.
# ---------------------------------------------------------------------------


def test_claude_merge_emits_turn_metrics_from_assistant_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cwd = Path("/x")
    _write_claude_session(
        tmp_path,
        cwd,
        "sess",
        [
            _claude_assistant_line(
                [{"type": "text", "text": "ok"}],
                "2026-05-05T11:00:01Z",
                model="claude-opus-4-7",
                usage={
                    "input_tokens": 1200,
                    "output_tokens": 340,
                    "cache_read_input_tokens": 5_000,
                    "cache_creation_input_tokens": 800,
                },
            ),
        ],
    )
    events = merge_cli_transcript(
        "claude-code",
        "sess",
        cwd,
        {"provider": "claude-code", "anchor_ts": "2026-05-05T10:00:00Z"},
    )
    assert [e["type"] for e in events] == ["message_complete", "turn_metrics"]
    metrics = events[1]
    assert metrics["input_tokens"] == 1200
    assert metrics["output_tokens"] == 340
    assert metrics["cache_read_input_tokens"] == 5_000
    assert metrics["cache_creation_input_tokens"] == 800
    assert metrics["model"] == "claude-opus-4-7"
    # CLI export carries no wall-clock; duration is reported as zero.
    assert metrics["duration_ms"] == 0
    # Each merged assistant message is one API call (CLI exports are
    # per-call, not aggregated like ResultMessage), so last_prompt_tokens
    # equals input + cache_read + cache_creation directly.
    assert metrics["last_prompt_tokens"] == 1200 + 5_000 + 800


def test_claude_merge_skips_turn_metrics_when_usage_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Older sessions or partial exports may omit ``usage`` entirely.
    Don't synthesise a zeroed-out metrics event — that would skew the
    cost rollup downward. Just emit the content blocks."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cwd = Path("/x")
    _write_claude_session(
        tmp_path,
        cwd,
        "sess",
        [
            _claude_assistant_line(
                [{"type": "text", "text": "ok"}],
                "2026-05-05T11:00:01Z",
                # no usage / no model
            ),
        ],
    )
    events = merge_cli_transcript(
        "claude-code",
        "sess",
        cwd,
        {"provider": "claude-code", "anchor_ts": "2026-05-05T10:00:00Z"},
    )
    assert [e["type"] for e in events] == ["message_complete"]


def test_amp_merge_emits_turn_metrics_from_assistant_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Amp's ``threads export`` uses the same Anthropic shape on assistant
    messages — usage lives at ``message.usage``, model at ``message.model``."""
    payload = _amp_export_payload(
        "T-foo",
        [
            {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "hi"}],
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        ],
    )
    _install_fake_amp(monkeypatch, stdout=payload)
    events = merge_cli_transcript(
        "amp", "T-foo", Path("/x"), {"provider": "amp", "message_count": 0}
    )
    assert [e["type"] for e in events] == ["message_complete", "turn_metrics"]
    metrics = events[1]
    assert metrics["input_tokens"] == 50
    assert metrics["output_tokens"] == 10
    assert metrics["model"] == "claude-sonnet-4-6"
