"""Unit tests for the Codex adapter's notification → AgentEvent mapping
plus full-lifecycle drive through a fake client factory.

The real ``openai-codex-sdk`` is exercised only by a manual smoke test
from a developer machine — these tests inject a fake ``CodexClient``
implementation matching the ``CodexClient`` / ``CodexThread`` /
``CodexTurnHandle`` Protocols defined alongside the adapter. That keeps
the suite hermetic + fast and exercises every notification variant.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.domain.agents import (
    AgentEvent,
    AgentStartContext,
    ArtifactMarker,
    CodexAgentConfig,
    CodexApprovalMode,
    CodexModel,
    CodexReasoningEffort,
    CodexSandbox,
    CommonAgentConfig,
    Error,
    MessageComplete,
    MessageDelta,
    PermissionDecision,
    PermissionRequest,
    SessionEstablished,
    StatusChange,
    ThinkingComplete,
    ThinkingDelta,
    ToolCall,
    ToolResult,
    TurnMetrics,
)
from src.infrastructure.agents.codex_adapter import (
    _APP_SERVER_STDIO_LIMIT_BYTES,
    CodexAdapter,
    _app_server_approval_result,
    _app_server_thread_params,
    _CodexAppServerTurnHandle,
    _CodexTokenSnapshotTail,
    _command_execution_args,
    _convert,
    _file_change_canonical,
    _normalize_app_server_notification,
    _normalize_sdk_event,
    _per_call_prompt_tokens,
    _thread_options_from_kwargs,
    _TokenSnapshot,
)

# ---------------------------------------------------------------------------
# Test doubles for the SDK Protocols
# ---------------------------------------------------------------------------


@dataclass
class FakeNotification:
    """Drop-in for the ``Notification`` Protocol."""

    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeApprovalRequest:
    """Drop-in for the ``ApprovalRequest`` Protocol."""

    request_id: str
    tool_name: str
    tool_input: dict[str, Any]


class FakeTurn:
    """One scripted turn — yields the supplied notifications, records
    whether ``interrupt`` was called."""

    def __init__(self, notifications: list[FakeNotification]) -> None:
        self._notifications = list(notifications)
        self.interrupted = False

    async def stream(self) -> AsyncIterator[FakeNotification]:
        for n in self._notifications:
            yield n

    async def interrupt(self) -> None:
        self.interrupted = True


class FakeThread:
    """In-memory Codex thread — returns scripted turns in order, records
    turn-start arguments so tests can assert on dispatch."""

    def __init__(self, thread_id: str, turns: list[FakeTurn]) -> None:
        self.id = thread_id
        self._turns = list(turns)
        self.received_inputs: list[str] = []

    async def turn_start(self, user_message: str) -> FakeTurn:
        self.received_inputs.append(user_message)
        if not self._turns:
            # If a test under-specifies, return an empty turn rather than
            # crashing — easier to triage than IndexError.
            return FakeTurn([])
        return self._turns.pop(0)


class FakeClient:
    """In-memory Codex client. Test injects via the ``client_factory`` seam."""

    def __init__(
        self,
        thread: FakeThread,
        *,
        resume_thread: FakeThread | None = None,
    ) -> None:
        self._thread = thread
        self._resume_thread = resume_thread
        self.entered = False
        self.exited = False
        self.start_kwargs: dict[str, Any] | None = None
        self.resume_kwargs: dict[str, Any] | None = None
        self.resume_thread_id: str | None = None
        self._approval_callback: Callable[..., Any] | None = None

    async def __aenter__(self) -> FakeClient:
        self.entered = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.exited = True

    async def thread_start(self, **kwargs: Any) -> FakeThread:
        self.start_kwargs = kwargs
        return self._thread

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> FakeThread:
        self.resume_thread_id = thread_id
        self.resume_kwargs = kwargs
        if self._resume_thread is None:
            return self._thread
        return self._resume_thread

    def on_approval_request(self, callback: Callable[..., Any]) -> None:
        self._approval_callback = callback

    async def fire_approval(self, request: FakeApprovalRequest) -> str:
        """Simulate the SDK invoking our approval callback."""
        assert self._approval_callback is not None
        result = await self._approval_callback(request)
        return str(result)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _common(
    *, writable_roots: tuple[Path, ...] = ()
) -> CommonAgentConfig:
    return CommonAgentConfig(
        workdir=Path("/tmp/codex-adapter-test"),
        system_prompt="you are a test agent",
        writable_roots=writable_roots,
    )


def _config(
    *,
    model: CodexModel = CodexModel.GPT_5_4,
    sandbox: CodexSandbox = CodexSandbox.WORKSPACE_WRITE,
    approval_mode: CodexApprovalMode = CodexApprovalMode.ON_REQUEST,
    reasoning_effort: CodexReasoningEffort = CodexReasoningEffort.MEDIUM,
    writable_roots: tuple[Path, ...] = (),
) -> CodexAgentConfig:
    return CodexAgentConfig(
        common=_common(writable_roots=writable_roots),
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox=sandbox,
        approval_mode=approval_mode,
    )


def _start_context(session_id: str | None = None) -> AgentStartContext:
    return AgentStartContext(
        workdir=Path("/tmp/codex-adapter-test"),
        model=CodexModel.GPT_5_4.value,
        system_prompt="you are a test agent",
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# _convert mapping tests
# ---------------------------------------------------------------------------


def test_message_delta_emits_message_delta() -> None:
    [event] = list(
        _convert(FakeNotification("item/agentMessage/delta", {"delta": "hel"}))
    )
    assert isinstance(event, MessageDelta)
    assert event.text == "hel"


def test_empty_delta_emits_nothing() -> None:
    """Codex occasionally sends a delta frame with no text (cursor
    flicker between sub-streams). We don't want a stream of empty
    MessageDelta events flooding the transcript."""
    assert list(_convert(FakeNotification("item/agentMessage/delta", {"delta": ""}))) == []


def test_reasoning_delta_emits_thinking_delta() -> None:
    [event] = list(
        _convert(
            FakeNotification(
                "item/reasoning/summaryTextDelta", {"delta": "considering"}
            )
        )
    )
    assert isinstance(event, ThinkingDelta)
    assert event.text == "considering"


def test_agent_message_completed_emits_message_complete() -> None:
    [event] = list(
        _convert(
            FakeNotification(
                "item/completed",
                {"item": {"itemType": "agentMessage", "text": "hello"}},
            )
        )
    )
    assert isinstance(event, MessageComplete)
    assert event.text == "hello"


def test_agent_message_completed_scans_for_artifact_marker() -> None:
    """Belt-and-suspenders artifact scan on every MessageComplete — same
    fallback Claude/Amp carry, in case the model emits the marker as a
    JSON line in chat instead of (or in addition to) the MCP tool."""
    text = (
        "Filed the PR.\n"
        '{"atelier_artifact": {"type": "pr", "url": "https://x/y/pull/3", "title": "fix bug"}}\n'
        "Done."
    )
    events = list(
        _convert(
            FakeNotification(
                "item/completed",
                {"item": {"itemType": "agentMessage", "text": text}},
            )
        )
    )
    assert len(events) == 2
    msg, marker = events
    assert isinstance(msg, MessageComplete)
    assert isinstance(marker, ArtifactMarker)
    assert marker.payload["type"] == "pr"
    assert marker.payload["url"] == "https://x/y/pull/3"


def test_reasoning_completed_emits_thinking_complete() -> None:
    [event] = list(
        _convert(
            FakeNotification(
                "item/completed",
                {"item": {"itemType": "reasoning", "text": "I think therefore"}},
            )
        )
    )
    assert isinstance(event, ThinkingComplete)
    assert event.text == "I think therefore"


def test_command_execution_started_emits_canonical_bash_tool_call() -> None:
    events = list(
        _convert(
            FakeNotification(
                "item/started",
                {
                    "item": {
                        "itemType": "commandExecution",
                        "id": "cmd-1",
                        "command": ["-c", "ls -la"],
                        "cwd": "/tmp/x",
                    }
                },
            )
        )
    )
    assert len(events) == 1
    assert isinstance(events[0], ToolCall)
    assert events[0].name == "Bash"
    assert events[0].arguments == {"command": "ls -la", "cwd": "/tmp/x"}
    assert events[0].tool_id == "cmd-1"


def test_command_execution_argv_form_falls_back_to_argv_dict() -> None:
    """Non ``["-c", "<cmd>"]`` argv shapes preserve the raw list under
    ``argv`` so the frontend's generic JSON view renders something
    meaningful."""
    args = _command_execution_args(
        {"command": ["git", "status"], "cwd": "/repo"}
    )
    assert args == {"argv": ["git", "status"], "cwd": "/repo"}


def test_command_execution_completed_emits_tool_result_with_error_flag() -> None:
    events = list(
        _convert(
            FakeNotification(
                "item/completed",
                {
                    "item": {
                        "itemType": "commandExecution",
                        "id": "cmd-1",
                        "output": "no such file",
                        "exit_code": 2,
                    }
                },
            )
        )
    )
    assert len(events) == 1
    assert isinstance(events[0], ToolResult)
    assert events[0].tool_id == "cmd-1"
    assert events[0].content == "no such file"
    assert events[0].is_error is True


def test_command_execution_completed_zero_exit_is_not_error() -> None:
    [event] = list(
        _convert(
            FakeNotification(
                "item/completed",
                {
                    "item": {
                        "itemType": "commandExecution",
                        "id": "cmd-1",
                        "output": "ok",
                        "exit_code": 0,
                    }
                },
            )
        )
    )
    assert isinstance(event, ToolResult)
    assert event.is_error is False


def test_file_change_started_with_old_and_new_maps_to_edit() -> None:
    events = list(
        _convert(
            FakeNotification(
                "item/started",
                {
                    "item": {
                        "itemType": "fileChange",
                        "id": "fc-1",
                        "path": "src/main.py",
                        "old_text": "import a",
                        "new_text": "import b",
                    }
                },
            )
        )
    )
    assert len(events) == 1
    assert isinstance(events[0], ToolCall)
    assert events[0].name == "Edit"
    assert events[0].arguments == {
        "path": "src/main.py",
        "old_text": "import a",
        "new_text": "import b",
    }


def test_file_change_started_empty_old_text_maps_to_write() -> None:
    """Empty ``old_text`` + full ``new_text`` is a new-file write —
    canonicalised to ``Write`` so the FE renders it under the same
    tool concept Claude/Amp use."""
    tool_name, args = _file_change_canonical(
        {"path": "src/new.py", "old_text": "", "new_text": "print(1)\n"}
    )
    assert tool_name == "Write"
    assert args == {"path": "src/new.py", "content": "print(1)\n"}


def test_file_change_started_camel_case_keys_are_accepted() -> None:
    """Codex notifications may surface ``oldText`` / ``newText`` (camel-
    case) on the wire; the converter normalises both shapes."""
    tool_name, args = _file_change_canonical(
        {"path": "src/x.py", "oldText": "a", "newText": "b"}
    )
    assert tool_name == "Edit"
    assert args == {"path": "src/x.py", "old_text": "a", "new_text": "b"}


def test_file_change_completed_emits_tool_result() -> None:
    [event] = list(
        _convert(
            FakeNotification(
                "item/completed",
                {
                    "item": {
                        "itemType": "fileChange",
                        "id": "fc-1",
                        "result": "patched",
                    }
                },
            )
        )
    )
    assert isinstance(event, ToolResult)
    assert event.tool_id == "fc-1"
    assert event.content == "patched"
    assert event.is_error is False


def test_normalize_sdk_event_keeps_unknown_file_change_fields() -> None:
    @dataclass
    class FakeUnknownFileChange:
        id: str
        type: str
        status: str
        changes: list[dict[str, str]]

    @dataclass
    class FakeSdkEvent:
        type: str
        item: FakeUnknownFileChange

    notification = _normalize_sdk_event(
        FakeSdkEvent(
            "item.started",
            FakeUnknownFileChange(
                id="fc-1",
                type="file_change",
                status="in_progress",
                changes=[{"path": "artifact.md", "kind": "add"}],
            ),
        )
    )

    assert notification.type == "item/started"
    item = notification.params["item"]
    assert item["itemType"] == "fileChange"
    assert item["status"] == "in_progress"
    assert item["path"] == "artifact.md"
    assert item["result"] == "add"


def test_mcp_tool_call_emits_tool_call() -> None:
    events = list(
        _convert(
            FakeNotification(
                "item/started",
                {
                    "item": {
                        "itemType": "mcpToolCall",
                        "id": "mcp-1",
                        "tool": "some_other_tool",
                        "arguments": {"x": 1},
                    }
                },
            )
        )
    )
    assert len(events) == 1
    assert isinstance(events[0], ToolCall)
    assert events[0].name == "some_other_tool"
    assert events[0].arguments == {"x": 1}


def test_mcp_record_pr_emits_artifact_marker_then_tool_call() -> None:
    """The Atelier artifact-recording tools produce both a marker (the
    side-effect signal the supervisor's tracker consumes) AND a regular
    ToolCall (so the chat shows the agent's invocation)."""
    events = list(
        _convert(
            FakeNotification(
                "item/started",
                {
                    "item": {
                        "itemType": "mcpToolCall",
                        "id": "mcp-1",
                        "tool": "mcp__atelier__record_pr",
                        "arguments": {
                            "url": "https://github.com/x/y/pull/3",
                            "title": "Add feature",
                        },
                    }
                },
            )
        )
    )
    assert len(events) == 2
    marker, call = events
    assert isinstance(marker, ArtifactMarker)
    assert marker.payload == {
        "type": "pr",
        "url": "https://github.com/x/y/pull/3",
        "title": "Add feature",
    }
    assert isinstance(call, ToolCall)
    assert call.name == "mcp__atelier__record_pr"


def test_turn_completed_emits_metrics_then_idle() -> None:
    events = list(
        _convert(
            FakeNotification(
                "turn/completed",
                {
                    "status": "completed",
                    "duration_ms": 1234,
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 80,
                        "cache_read_input_tokens": 12,
                        "cache_creation_input_tokens": 3,
                    },
                },
            ),
            model="gpt-5.4",
            last_prompt_tokens=98_400,
            context_window=258_400,
        )
    )
    assert len(events) == 2
    metrics, idle = events
    assert isinstance(metrics, TurnMetrics)
    assert metrics.duration_ms == 1234
    assert metrics.input_tokens == 200
    assert metrics.output_tokens == 80
    assert metrics.cache_read_input_tokens == 12
    assert metrics.cache_creation_input_tokens == 3
    assert metrics.last_prompt_tokens == 98_400
    assert metrics.model == "gpt-5.4"
    assert metrics.context_window == 258_400
    assert isinstance(idle, StatusChange)
    assert idle.status == "idle"


def test_turn_completed_failed_emits_error_before_metrics() -> None:
    events = list(
        _convert(
            FakeNotification(
                "turn/completed",
                {"status": "failed", "error": "boom", "duration_ms": 5},
            )
        )
    )
    assert len(events) == 3
    err, metrics, idle = events
    assert isinstance(err, Error)
    assert "boom" in err.message
    assert isinstance(metrics, TurnMetrics)
    assert isinstance(idle, StatusChange)
    assert idle.status == "idle"


def test_turn_completed_splits_codex_cached_input_usage() -> None:
    events = list(
        _convert(
            FakeNotification(
                "turn/completed",
                {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 1_000,
                        "cached_input_tokens": 775,
                        "output_tokens": 80,
                    },
                },
            ),
        )
    )
    metrics = events[0]
    assert isinstance(metrics, TurnMetrics)
    assert metrics.input_tokens == 225
    assert metrics.cache_read_input_tokens == 775
    assert metrics.output_tokens == 80


def test_turn_started_emits_no_events() -> None:
    """The pump already published ``StatusChange("thinking")`` when it
    popped the user's message — emitting a second one would just create
    duplicate transcript noise."""
    assert list(_convert(FakeNotification("turn/started", {}))) == []


def test_unknown_notification_type_is_dropped() -> None:
    """Forward-compatibility: unknown frame types (handshake / heartbeat /
    future variants) drop silently. Same posture as the Claude/Amp
    adapters take for SDK-internal frames."""
    assert list(_convert(FakeNotification("session/heartbeat", {}))) == []


def test_normalize_sdk_event_keeps_codex_token_count_payload() -> None:
    @dataclass
    class FakeSdkEvent:
        type: str
        payload: dict[str, Any]

    event = FakeSdkEvent(
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "last_token_usage": {"input_tokens": 240},
                "model_context_window": 258_400,
            },
        },
    )

    notification = _normalize_sdk_event(event)

    assert notification.type == "token_count"
    assert notification.params["info"]["model_context_window"] == 258_400


def test_per_call_prompt_tokens_pulls_from_agent_message_completion() -> None:
    """Codex's ``item/completed`` for ``agentMessage`` may carry a
    per-call usage block — that's the value we want for ctx%. Sum of
    input + cache_read + cache_creation; output excluded."""
    n = FakeNotification(
        "item/completed",
        {
            "item": {
                "itemType": "agentMessage",
                "text": "hi",
                "usage": {
                    "input_tokens": 180,
                    "output_tokens": 40,
                    "cache_read_input_tokens": 11_500,
                    "cache_creation_input_tokens": 320,
                },
            }
        },
    )
    assert _per_call_prompt_tokens(n) == 12_000


def test_per_call_prompt_tokens_treats_codex_cached_as_input_subset() -> None:
    n = FakeNotification(
        "item/completed",
        {
            "item": {
                "itemType": "agentMessage",
                "text": "hi",
                "usage": {
                    "input_tokens": 12_000,
                    "cached_input_tokens": 11_500,
                    "output_tokens": 40,
                },
            }
        },
    )
    assert _per_call_prompt_tokens(n) == 12_000


def test_per_call_prompt_tokens_returns_none_without_usage() -> None:
    n = FakeNotification(
        "item/completed", {"item": {"itemType": "agentMessage", "text": "hi"}}
    )
    assert _per_call_prompt_tokens(n) is None


def test_per_call_prompt_tokens_returns_none_for_non_message_items() -> None:
    """ctx% only tracks the model's prompt, not tool round-trips."""
    n = FakeNotification(
        "item/completed",
        {"item": {"itemType": "commandExecution", "exit_code": 0}},
    )
    assert _per_call_prompt_tokens(n) is None


# ---------------------------------------------------------------------------
# Adapter lifecycle tests
# ---------------------------------------------------------------------------


def test_start_emits_session_established_with_thread_id() -> None:
    thread = FakeThread(thread_id="thread-abc", turns=[])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> AgentEvent:
        await adapter.start(_start_context())
        # The session_established frame is the first thing the supervisor
        # would see — drain just that one without sending input.
        events_iter = adapter.events()
        try:
            return await events_iter.__anext__()
        finally:
            await adapter.close()

    first = asyncio.run(session())
    assert isinstance(first, SessionEstablished)
    assert first.session_id == "thread-abc"
    assert client.entered is True
    assert client.start_kwargs is not None
    assert client.start_kwargs["model"] == "gpt-5.4"
    assert client.start_kwargs["sandbox"] == "workspace-write"
    assert client.start_kwargs["approval_mode"] == "on-request"
    assert client.start_kwargs["base_instructions"] == "you are a test agent"
    assert client.start_kwargs["config_overrides"] == {
        "model_reasoning_effort": "medium"
    }


def test_start_forwards_writable_roots_as_additional_directories() -> None:
    thread = FakeThread(thread_id="thread-abc", turns=[])
    client = FakeClient(thread)
    adapter = CodexAdapter(
        _config(
            writable_roots=(
                Path("/Users/me/Atelier/projects/PRJ-001/shared/bmad"),
                Path("/Volumes/shared/specs"),
            )
        ),
        client_factory=lambda: client,
    )

    async def session() -> None:
        await adapter.start(_start_context())
        await adapter.close()

    asyncio.run(session())
    assert client.start_kwargs is not None
    assert client.start_kwargs["additional_directories"] == [
        "/Users/me/Atelier/projects/PRJ-001/shared/bmad",
        "/Volumes/shared/specs",
    ]


def test_start_skips_writable_roots_outside_workspace_write() -> None:
    thread = FakeThread(thread_id="thread-abc", turns=[])
    client = FakeClient(thread)
    adapter = CodexAdapter(
        _config(
            sandbox=CodexSandbox.READ_ONLY,
            writable_roots=(Path("/tmp/shared"),),
        ),
        client_factory=lambda: client,
    )

    async def session() -> None:
        await adapter.start(_start_context())
        await adapter.close()

    asyncio.run(session())
    assert client.start_kwargs is not None
    assert "additional_directories" not in client.start_kwargs


def test_thread_options_forward_additional_directories_to_sdk_alias() -> None:
    options = _thread_options_from_kwargs(
        {
            "model": "gpt-5.5",
            "cwd": "/tmp/worktree",
            "sandbox": "workspace-write",
            "approval_mode": "on-request",
            "additional_directories": ["/tmp/shared"],
        }
    )
    assert options["additionalDirectories"] == ["/tmp/shared"]


def test_app_server_thread_params_forward_writable_roots_to_config() -> None:
    params = _app_server_thread_params(
        model="gpt-5.5",
        cwd="/tmp/worktree",
        sandbox="workspace-write",
        approval_mode="on-request",
        base_instructions="system",
        mcp_servers=None,
        config_overrides={"model_reasoning_effort": "medium"},
        additional_directories=["/tmp/shared"],
    )

    assert params["approvalPolicy"] == "on-request"
    assert params["approvalsReviewer"] == "user"
    assert params["baseInstructions"] == "system"
    assert params["config"]["model_reasoning_effort"] == "medium"
    assert params["config"]["sandbox_workspace_write"] == {
        "writable_roots": ["/tmp/shared"],
        "network_access": False,
    }


def test_app_server_notifications_normalize_to_existing_codex_shape() -> None:
    notification = _normalize_app_server_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "type": "commandExecution",
                    "id": "cmd-1",
                    "command": "dt pytest",
                    "aggregatedOutput": "ok",
                    "exitCode": 0,
                },
            },
        }
    )

    assert notification is not None
    assert notification.type == "item/completed"
    assert notification.params["item"]["itemType"] == "commandExecution"
    assert notification.params["item"]["output"] == "ok"
    assert notification.params["item"]["exit_code"] == 0


def test_app_server_approval_result_maps_domain_decisions() -> None:
    assert _app_server_approval_result(
        "item/commandExecution/requestApproval", {}, "allow"
    ) == {"decision": "accept"}
    assert _app_server_approval_result(
        "item/commandExecution/requestApproval", {}, "allow_always"
    ) == {"decision": "acceptForSession"}
    assert _app_server_approval_result(
        "item/commandExecution/requestApproval", {}, "deny"
    ) == {"decision": "decline"}
    assert _app_server_approval_result(
        "item/permissions/requestApproval",
        {"permissions": {"network": {"enabled": True}}},
        "allow_always",
    ) == {
        "permissions": {"network": {"enabled": True}},
        "scope": "session",
    }


def test_resume_calls_thread_resume_with_session_id() -> None:
    """When ``context.session_id`` is set, the adapter calls
    ``thread_resume`` instead of ``thread_start`` so the Codex thread
    reopens on the same id."""
    thread = FakeThread(thread_id="thread-orig", turns=[])
    resume_thread = FakeThread(thread_id="thread-orig", turns=[])
    client = FakeClient(thread, resume_thread=resume_thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> None:
        await adapter.start(_start_context(session_id="thread-orig"))
        await adapter.close()

    asyncio.run(session())
    assert client.start_kwargs is None
    assert client.resume_thread_id == "thread-orig"
    assert client.resume_kwargs is not None


def test_full_lifecycle_translates_scripted_session() -> None:
    """Drive one turn end-to-end: SessionEstablished → thinking → deltas →
    MessageComplete → TurnMetrics → idle."""
    notifications = [
        FakeNotification("turn/started", {}),
        FakeNotification("item/agentMessage/delta", {"delta": "hel"}),
        FakeNotification("item/agentMessage/delta", {"delta": "lo"}),
        FakeNotification(
            "item/completed",
            {"item": {"itemType": "agentMessage", "text": "hello"}},
        ),
        FakeNotification(
            "turn/completed",
            {
                "status": "completed",
                "duration_ms": 42,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ),
    ]
    thread = FakeThread("thread-1", [FakeTurn(notifications)])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        await adapter.send_input("hi")
        events: list[AgentEvent] = []
        async for ev in adapter.events():
            events.append(ev)
            if isinstance(ev, StatusChange) and ev.status == "idle":
                break
        await adapter.close()
        return events

    events = asyncio.run(session())
    types = [type(e) for e in events]
    assert types == [
        SessionEstablished,
        StatusChange,  # thinking (from the pump)
        MessageDelta,
        MessageDelta,
        MessageComplete,
        TurnMetrics,
        StatusChange,  # idle
    ]
    assert thread.received_inputs == ["hi"]


def test_full_lifecycle_uses_codex_token_count_for_turn_metrics_context() -> None:
    notifications = [
        FakeNotification("turn/started", {}),
        FakeNotification(
            "token_count",
            {
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1_000,
                        "cached_input_tokens": 775,
                        "output_tokens": 80,
                    },
                    "last_token_usage": {
                        "input_tokens": 240,
                        "cached_input_tokens": 120,
                        "output_tokens": 18,
                    },
                    "model_context_window": 258_400,
                }
            },
        ),
        FakeNotification(
            "turn/completed",
            {
                "status": "completed",
                "usage": {
                    "input_tokens": 1_000,
                    "cached_input_tokens": 775,
                    "output_tokens": 80,
                },
            },
        ),
    ]
    thread = FakeThread("thread-1", [FakeTurn(notifications)])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> TurnMetrics:
        await adapter.start(_start_context())
        await adapter.send_input("hi")
        async for ev in adapter.events():
            if isinstance(ev, TurnMetrics):
                await adapter.close()
                return ev
        raise AssertionError("missing metrics")

    metrics = asyncio.run(session())
    assert metrics.input_tokens == 225
    assert metrics.cache_read_input_tokens == 775
    assert metrics.output_tokens == 80
    assert metrics.last_prompt_tokens == 240
    assert metrics.context_window == 258_400


def test_full_lifecycle_polls_codex_session_for_turn_metrics_context() -> None:
    """The SDK can omit token_count frames while Codex writes them to JSONL."""
    notifications = [
        FakeNotification("turn/started", {}),
        FakeNotification(
            "turn/completed",
            {
                "status": "completed",
                "usage": {
                    "input_tokens": 1_000,
                    "cached_input_tokens": 775,
                    "output_tokens": 80,
                },
            },
        ),
    ]
    thread = FakeThread("thread-1", [FakeTurn(notifications)])
    client = FakeClient(thread)
    # First poll primes the adapter at turn start; second poll sees the
    # current turn's token_count snapshot before turn/completed converts.
    snapshots = iter([None, _TokenSnapshot(240, 258_400), None])
    adapter = CodexAdapter(
        _config(),
        client_factory=lambda: client,
        token_snapshot_poller=lambda _session_id: next(snapshots, None),
    )

    async def session() -> TurnMetrics:
        await adapter.start(_start_context())
        await adapter.send_input("hi")
        async for ev in adapter.events():
            if isinstance(ev, TurnMetrics):
                await adapter.close()
                return ev
        raise AssertionError("missing metrics")

    metrics = asyncio.run(session())
    assert metrics.last_prompt_tokens == 240
    assert metrics.context_window == 258_400


def test_codex_token_snapshot_tail_reads_incremental_session_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    session_id = "session-1"
    path = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "05"
        / "25"
        / f"rollout-2026-05-25T12-00-00-{session_id}.jsonl"
    )
    path.parent.mkdir(parents=True)

    def write_token_count(input_tokens: int) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": input_tokens,
                                    "cached_input_tokens": input_tokens - 10,
                                    "output_tokens": 1,
                                },
                                "model_context_window": 258_400,
                            },
                        },
                    }
                )
                + "\n"
            )

    write_token_count(100)
    tail = _CodexTokenSnapshotTail()

    first = tail.poll(session_id)
    assert first == _TokenSnapshot(last_prompt_tokens=100, context_window=258_400)

    write_token_count(240)
    second = tail.poll(session_id)
    assert second == _TokenSnapshot(last_prompt_tokens=240, context_window=258_400)
    assert tail.poll(session_id) is None


def test_close_is_idempotent() -> None:
    thread = FakeThread("thread-1", [])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> None:
        await adapter.start(_start_context())
        await adapter.close()
        await adapter.close()  # must not raise

    asyncio.run(session())
    assert client.exited is True


def test_start_twice_raises() -> None:
    thread = FakeThread("thread-1", [])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> None:
        await adapter.start(_start_context())
        with pytest.raises(RuntimeError, match="start\\(\\) called twice"):
            await adapter.start(_start_context())
        await adapter.close()

    asyncio.run(session())


def test_events_before_start_raises() -> None:
    thread = FakeThread("thread-1", [])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> None:
        with pytest.raises(RuntimeError, match="events\\(\\) called before start"):
            async for _ in adapter.events():
                pass

    asyncio.run(session())


# ---------------------------------------------------------------------------
# Approval routing
# ---------------------------------------------------------------------------


def test_approval_request_emits_permission_request_and_resolves_with_decision() -> None:
    thread = FakeThread("thread-1", [])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> tuple[str, list[AgentEvent]]:
        await adapter.start(_start_context())
        events: list[AgentEvent] = []

        async def drain() -> None:
            async for ev in adapter.events():
                events.append(ev)
                if (
                    isinstance(ev, PermissionRequest)
                    and ev.tool_name == "Bash"
                ):
                    await adapter.resolve_permission(ev.request_id, "allow")
                if isinstance(ev, PermissionDecision):
                    return

        drain_task = asyncio.create_task(drain())
        # Fire the approval after the pump is up.
        await asyncio.sleep(0)
        decision = await client.fire_approval(
            FakeApprovalRequest(
                request_id="req-1",
                tool_name="Bash",
                tool_input={"command": "ls"},
            )
        )
        await drain_task
        await adapter.close()
        return decision, events

    decision, events = asyncio.run(session())
    assert decision == "allow"
    types = [type(e) for e in events]
    assert SessionEstablished in types
    assert PermissionRequest in types
    assert PermissionDecision in types
    request = next(e for e in events if isinstance(e, PermissionRequest))
    assert request.tool_name == "Bash"
    assert request.tool_input == {"command": "ls"}
    decision_ev = next(e for e in events if isinstance(e, PermissionDecision))
    assert decision_ev.request_id == request.request_id
    assert decision_ev.decision == "allow"


def test_approval_request_short_circuits_on_allow_always() -> None:
    """Once the user picks 'allow always' for a canonical tool name, the
    adapter skips the prompt + decision events for subsequent invocations
    of the same tool — same behaviour as the Claude/Amp adapters."""
    thread = FakeThread("thread-1", [])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        events: list[AgentEvent] = []
        resolved_first = False

        async def drain() -> None:
            nonlocal resolved_first
            async for ev in adapter.events():
                events.append(ev)
                if isinstance(ev, PermissionRequest) and not resolved_first:
                    resolved_first = True
                    await adapter.resolve_permission(
                        ev.request_id, "allow_always"
                    )
                if (
                    isinstance(ev, PermissionDecision)
                    and ev.decision == "allow_always"
                ):
                    # Second approval should auto-allow → no further
                    # events. Fire it now and short-circuit drain.
                    return

        drain_task = asyncio.create_task(drain())
        await asyncio.sleep(0)
        first = await client.fire_approval(
            FakeApprovalRequest(
                request_id="req-1",
                tool_name="Bash",
                tool_input={"command": "ls"},
            )
        )
        await drain_task
        # Second approval after allow_always → returns "allow" directly,
        # no PermissionRequest / PermissionDecision event pair.
        second = await client.fire_approval(
            FakeApprovalRequest(
                request_id="req-2",
                tool_name="Bash",
                tool_input={"command": "pwd"},
            )
        )
        await adapter.close()
        assert first == "allow_always"
        assert second == "allow"
        return events

    events = asyncio.run(session())
    # Only one PermissionRequest / PermissionDecision pair should be in
    # the transcript despite two approvals firing.
    requests = [e for e in events if isinstance(e, PermissionRequest)]
    decisions = [e for e in events if isinstance(e, PermissionDecision)]
    assert len(requests) == 1
    assert len(decisions) == 1
    assert decisions[0].decision == "allow_always"


def test_close_denies_in_flight_permission_prompts() -> None:
    """Shutdown safety: any open approval request gets a synthetic
    ``deny`` decision so the transcript never carries an orphan
    ``permission_request`` without a matching decision. Otherwise the
    frontend rebuilds ``pendingPermissions`` from the transcript on
    reconnect and the prompt stays stuck."""
    thread = FakeThread("thread-1", [])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        events: list[AgentEvent] = []
        seen_request = asyncio.Event()

        async def drain() -> None:
            async for ev in adapter.events():
                events.append(ev)
                if isinstance(ev, PermissionRequest):
                    seen_request.set()

        drain_task = asyncio.create_task(drain())
        approval_task = asyncio.create_task(
            client.fire_approval(
                FakeApprovalRequest(
                    request_id="req-1",
                    tool_name="Bash",
                    tool_input={"command": "rm -rf /"},
                )
            )
        )
        await seen_request.wait()
        await adapter.close()
        decision = await approval_task
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        assert decision == "deny"
        return events

    events = asyncio.run(session())
    decisions = [e for e in events if isinstance(e, PermissionDecision)]
    assert len(decisions) == 1
    assert decisions[0].decision == "deny"


# ---------------------------------------------------------------------------
# stop_turn
# ---------------------------------------------------------------------------


def test_stop_turn_interrupts_in_flight_turn() -> None:
    """A long-running turn (notification iterator that awaits) gets
    interrupted when ``stop_turn`` fires. The fake turn mirrors the real
    SDK behaviour: ``interrupt()`` releases the stream which then drains
    a final ``turn/completed`` (status=interrupted) so the pump emits
    TurnMetrics + idle on the way back to the prompt iterator."""

    class BlockingTurn:
        def __init__(self) -> None:
            self.interrupted = False
            self._released = asyncio.Event()

        async def stream(self) -> AsyncIterator[FakeNotification]:
            yield FakeNotification("turn/started", {})
            await self._released.wait()
            yield FakeNotification(
                "turn/completed",
                {"status": "interrupted", "duration_ms": 0},
            )

        async def interrupt(self) -> None:
            self.interrupted = True
            self._released.set()

    turn = BlockingTurn()
    thread = FakeThread("thread-1", [turn])  # type: ignore[list-item]
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> bool:
        await adapter.start(_start_context())
        events_task = asyncio.create_task(_drain_until_idle(adapter))
        await adapter.send_input("do work")
        # Give the pump time to enter the stream loop.
        await asyncio.sleep(0.05)
        await adapter.stop_turn()
        await asyncio.wait_for(events_task, timeout=2.0)
        await adapter.close()
        return turn.interrupted

    interrupted = asyncio.run(session())
    assert interrupted is True


def test_app_server_interrupt_synthesizes_terminal_turn() -> None:
    """The Codex app-server may acknowledge interrupt without sending a
    terminal turn/completed notification. The handle must still unblock
    the adapter pump so queued follow-up inputs are processed."""

    class FakeAppServerClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def _interrupt_turn(self, thread_id: str, turn_id: str) -> None:
            self.calls.append((thread_id, turn_id))

    client = FakeAppServerClient()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    handle = _CodexAppServerTurnHandle(  # type: ignore[arg-type]
        client, "thread-1", "turn-1", queue
    )

    async def session() -> Any:
        async def read_one() -> Any:
            async for event in handle.stream():
                return event
            raise AssertionError("stream ended without a terminal event")

        stream_task = asyncio.create_task(read_one())
        await asyncio.sleep(0)
        await handle.interrupt()
        return await asyncio.wait_for(stream_task, timeout=1.0)

    event = asyncio.run(session())
    assert client.calls == [("thread-1", "turn-1")]
    assert event.type == "turn/completed"
    assert event.params["status"] == "interrupted"
    assert event.params["turnId"] == "turn-1"


def test_app_server_stdio_limit_handles_large_resume_payloads() -> None:
    """thread/resume responses include historical turns and can exceed
    asyncio's default 64 KiB readline limit for long Codex sessions."""

    assert _APP_SERVER_STDIO_LIMIT_BYTES >= 1024 * 1024


def test_stop_turn_before_first_turn_is_a_noop() -> None:
    thread = FakeThread("thread-1", [])
    client = FakeClient(thread)
    adapter = CodexAdapter(_config(), client_factory=lambda: client)

    async def session() -> None:
        await adapter.start(_start_context())
        # No turn in flight — must not raise.
        await adapter.stop_turn()
        await adapter.stop_turn()  # idempotent
        await adapter.close()

    asyncio.run(session())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drain_until_idle(adapter: CodexAdapter) -> list[AgentEvent]:
    """Drain ``events()`` until the next ``StatusChange("idle")`` arrives
    (or the stream ends). Used by lifecycle tests that need to observe a
    full turn without leaving the pump task hanging."""
    out: list[AgentEvent] = []
    async for ev in adapter.events():
        out.append(ev)
        if isinstance(ev, StatusChange) and ev.status == "idle":
            return out
    return out
