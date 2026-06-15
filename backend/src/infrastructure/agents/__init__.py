"""Concrete agent adapters + the build_adapter dispatch.

Importing the per-provider modules here is what registers each
``@build_adapter.register`` handler at startup, so callers can rely on
``build_adapter(config, settings)`` finding the right adapter.
"""

import src.infrastructure.agents.acp.providers  # noqa: F401  (ACP registrations)
from src.infrastructure.agents.acp import AcpAdapter
from src.infrastructure.agents.amp_adapter import AmpAdapter
from src.infrastructure.agents.claude_code_adapter import (
    ClaudeCodeAdapter,
)
from src.infrastructure.agents.codex_adapter import CodexAdapter
from src.infrastructure.agents.factory import build_adapter
from src.infrastructure.agents.stub_adapter import StubAgentAdapter

__all__ = [
    "AcpAdapter",
    "AmpAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "StubAgentAdapter",
    "build_adapter",
]
