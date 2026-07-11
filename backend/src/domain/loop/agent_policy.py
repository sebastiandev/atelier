"""Map generic loop permissions onto provider runtime options."""

from __future__ import annotations

from src.domain.agents.configs import (
    AmpPermissionMode,
    ClaudeAcpPermissionMode,
    ClaudePermissionMode,
    CodexAcpMode,
    CodexApprovalMode,
    CodexSandbox,
    OpenCodeMode,
)
from src.domain.loop.dtos import LoopAgentPolicy, LoopPermission
from src.domain.models import Provider


def apply_stage_agent_policy(
    provider: Provider,
    base_options: dict[str, object],
    policy: LoopAgentPolicy,
) -> dict[str, object]:
    """Apply one stage's generic effort and permission posture.

    Preconditions: ``base_options`` belongs to ``provider``.
    Postconditions: returns new provider-valid options without mutating input.
    """
    options = dict(base_options)
    write = policy.permissions == LoopPermission.WRITE
    if policy.effort:
        key = (
            "reasoning_effort"
            if provider in {"codex", "codex-acp"}
            else "thinking_effort"
        )
        options[key] = policy.effort
    if provider == "codex":
        options.update(
            sandbox=(
                CodexSandbox.WORKSPACE_WRITE.value
                if write
                else CodexSandbox.READ_ONLY.value
            ),
            approval_mode=CodexApprovalMode.NEVER.value,
        )
    elif provider == "codex-acp":
        options["mode"] = (
            CodexAcpMode.FULL_ACCESS.value if write else CodexAcpMode.READ_ONLY.value
        )
    elif provider == "claude-code":
        options["permission_mode"] = (
            ClaudePermissionMode.BYPASS.value
            if write
            else ClaudePermissionMode.PLAN.value
        )
    elif provider == "claude-acp":
        options["permission_mode"] = (
            ClaudeAcpPermissionMode.BYPASS.value
            if write
            else ClaudeAcpPermissionMode.PLAN.value
        )
    elif provider == "amp":
        options["permission_mode"] = (
            AmpPermissionMode.ALLOW_ALL.value
            if write
            else AmpPermissionMode.DEFAULT.value
        )
    elif provider == "opencode":
        options["mode"] = OpenCodeMode.BUILD.value if write else OpenCodeMode.PLAN.value
    return options


__all__ = ["apply_stage_agent_policy"]
