"""Agent boundary: AgentAdapter port, AgentConfig hierarchy, AgentEvent union."""

from src.domain.agents.configs import (
    AgentConfig,
    AmpAgentConfig,
    AmpMode,
    ClaudeAgentConfig,
    ClaudeEffort,
    ClaudeModel,
    ClaudePermissionMode,
    CommonAgentConfig,
)
from src.domain.agents.events import (
    AgentEvent,
    ArtifactMarker,
    Error,
    MessageComplete,
    MessageDelta,
    StatusChange,
    ThinkingComplete,
    ThinkingDelta,
    ToolCall,
    ToolResult,
    TurnMetrics,
)
from src.domain.agents.ports import AgentAdapter, AgentStartContext
from src.domain.agents.specs import (
    SPECS,
    AmpSpec,
    ClaudeSpec,
    EnumOption,
    ProviderDescriptor,
    Spec,
)
from src.domain.agents.system_prompt import render_system_prompt

__all__ = [
    "SPECS",
    "AgentAdapter",
    "AgentConfig",
    "AgentEvent",
    "AgentStartContext",
    "AmpAgentConfig",
    "AmpMode",
    "AmpSpec",
    "ArtifactMarker",
    "ClaudeAgentConfig",
    "ClaudeEffort",
    "ClaudeModel",
    "ClaudePermissionMode",
    "ClaudeSpec",
    "CommonAgentConfig",
    "EnumOption",
    "Error",
    "MessageComplete",
    "MessageDelta",
    "ProviderDescriptor",
    "Spec",
    "StatusChange",
    "ThinkingComplete",
    "ThinkingDelta",
    "ToolCall",
    "ToolResult",
    "TurnMetrics",
    "render_system_prompt",
]
