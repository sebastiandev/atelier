"""Concrete agent adapters + the build_adapter dispatch.

Importing the per-provider modules here is what registers each
``@build_adapter.register`` handler at startup, so callers can rely on
``build_adapter(config, settings)`` finding the right adapter.
"""

from src.infrastructure.agents.factory import build_adapter
from src.infrastructure.agents.stub_adapter import StubAgentAdapter
from src.infrastructure.agents.amp_adapter import AmpAdapter  # noqa: F401  (registers)
from src.infrastructure.agents.claude_code_adapter import ClaudeCodeAdapter  # noqa: F401  (registers)

__all__ = ["AmpAdapter", "ClaudeCodeAdapter", "StubAgentAdapter", "build_adapter"]
