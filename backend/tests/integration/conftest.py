"""Integration-test fixtures.

The integration suite covers the supervisor / WS / persistence pipeline
end-to-end. Several tests under ``test_agents_routes.py`` create an
agent via ``provider="amp"`` and then assert against a deterministic
15-event sequence on the WS — that sequence was historically the
walking-skeleton fixture baked into ``AmpAdapter`` while it was stub-
backed. Now that ``AmpAdapter`` drives the real Amp CLI, we restore
the deterministic behaviour for these tests by re-registering the
``AmpAgentConfig`` handler to return a ``StubAgentAdapter`` replaying
the canned demo. The override is reversed at teardown so other test
modules see the production wiring.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.agents import (
    AgentEvent,
    AmpAgentConfig,
    MessageComplete,
    MessageDelta,
    StatusChange,
    ToolCall,
    ToolResult,
)
from src.infrastructure.agents import StubAgentAdapter
from src.infrastructure.agents.factory import build_adapter
from src.settings import Settings


def _canned_demo_events() -> list[AgentEvent]:
    """The walking-skeleton 15-event demo, used by the WS integration tests."""
    now = datetime.now(UTC)
    return [
        StatusChange(ts=now, status="thinking"),
        MessageDelta(ts=now, text="Hello! I'm "),
        MessageDelta(ts=now, text="a stub agent "),
        MessageDelta(ts=now, text="for the walking-skeleton."),
        MessageComplete(ts=now, text="Hello! I'm a stub agent for the walking-skeleton."),
        StatusChange(ts=now, status="thinking"),
        MessageDelta(ts=now, text="Let me try a tool call."),
        MessageComplete(ts=now, text="Let me try a tool call."),
        ToolCall(
            ts=now,
            tool_id="t-1",
            name="read_file",
            arguments={"path": "~/notes.md"},
        ),
        ToolResult(ts=now, tool_id="t-1", content="(simulated) file not found"),
        MessageDelta(ts=now, text="Got "),
        MessageDelta(ts=now, text="the result. "),
        MessageDelta(ts=now, text="That's all for now."),
        MessageComplete(ts=now, text="Got the result. That's all for now."),
        StatusChange(ts=now, status="idle"),
    ]


@pytest.fixture
def tmp_workdir(tmp_path: Path) -> str:
    """A real on-disk directory tests can pass as an Agent's ``folder``.

    ``start_agent`` validates the folder exists (otherwise the eventual
    subprocess ``cwd`` raises ENOENT). Tests that POST agents need a real
    path; this fixture gives them one inside the per-test tmp tree.
    """
    folder = tmp_path / "workdir"
    folder.mkdir()
    return str(folder)


@pytest.fixture(autouse=True)
def stub_amp_dispatch() -> Iterator[None]:
    """Swap AmpAdapter for a canned StubAgentAdapter in integration tests.

    The integration suite never has live amp credentials and would hang
    on a real CLI subprocess. Re-registering the singledispatch handler
    is the simplest reversible swap; we capture the production handler
    on entry and restore it on teardown so module ordering can't leak
    state into unit tests that share the same process.
    """
    original = build_adapter.dispatch(AmpAgentConfig)

    def _stub(config: AmpAgentConfig, settings: Settings) -> StubAgentAdapter:
        return StubAgentAdapter(_canned_demo_events())

    build_adapter.register(AmpAgentConfig)(_stub)
    try:
        yield
    finally:
        build_adapter.register(AmpAgentConfig)(original)
