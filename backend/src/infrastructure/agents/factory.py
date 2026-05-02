"""Adapter factory: dispatch from a typed AgentConfig to a concrete adapter.

Wired with ``functools.singledispatch`` over the ``AgentConfig`` union.
Each provider's adapter module registers itself by decorating a builder
with ``@build_adapter.register``. The package's ``__init__`` imports
those modules so registration happens at startup.

The default branch raises so an un-registered provider fails loudly at
the call site rather than silently constructing the wrong adapter.
"""

from functools import singledispatch

from src.domain.agents import AgentAdapter, AgentConfig
from src.settings import Settings


@singledispatch
def build_adapter(config: AgentConfig, settings: Settings) -> AgentAdapter:
    raise NotImplementedError(
        f"No adapter registered for AgentConfig type {type(config).__name__}"
    )


__all__ = ["build_adapter"]
