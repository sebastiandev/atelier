"""What a chat is allowed to do, declared by whoever created it.

A chat used to infer its own permissions: the runtime matched
``chat.title == "Planning"`` and read the ``discussion_only`` flag, then
applied that owner's hardcoded policy. Renaming a chat changed what it
could touch, adding a third owner meant editing the chat domain, and the
two owners' provider tables drifted apart.

Now the owner states a role at creation and the chat executes it. Roles
carry two things: how insistently the provider asks before acting, and a
conduct paragraph rendered into the system prompt.

Conduct is load-bearing, not decorative. No provider option expresses
"temporary files yes, source edits no" -- only Codex scopes writes by
path, and Claude/OpenCode have no equivalent. So for ``ADVISORY`` the
boundary is the conduct text plus a visible approval on every action.
The dataclass deliberately has no ``writes`` field, because a field like
that would promise enforcement two providers cannot deliver.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.domain.models import Provider


class ChatRole(StrEnum):
    """Who owns a chat, and therefore what it is for.

    - ``EXPLORE`` -- an ad-hoc chat. The user picked its options; Atelier
      does not override them.
    - ``ADVISORY`` -- a run discussion. Answers questions about a loop
      run and may write throwaway notes, but never implements.
    - ``PLANNING`` -- the reserved per-work Planning chat. Authors the
      plan documents once materialization has produced them.
    """

    EXPLORE = "explore"
    ADVISORY = "advisory"
    PLANNING = "planning"


class Approvals(StrEnum):
    """How much the provider asks before acting.

    - ``INHERIT`` -- leave the creator's options untouched.
    - ``ALWAYS`` -- prompt before every action.
    - ``ON_REQUEST`` -- act inside the writable roots, prompt to leave them.
    """

    INHERIT = "inherit"
    ALWAYS = "always"
    ON_REQUEST = "on_request"


ADVISORY_CONDUCT = (
    "You are not allowed to implement changes. Do not edit project source, "
    "tests, or configuration, and do not commit -- even if the user asks. "
    "You may write temporary plans or investigations when they help answer "
    "the question; keep them clearly temporary and outside the project's "
    "tracked sources. When you have identified a change, describe it and "
    "let the user route it back to the loop, which stays authoritative "
    "over the codebase."
)

PLANNING_CONDUCT = (
    "You own the plan documents in your working directory: read and edit "
    "them directly. Do not implement the plan -- no edits to project "
    "source, tests, or configuration, and no commits. Execution belongs to "
    "the agents the plan hands work to."
)


@dataclass(frozen=True)
class ChatPosture:
    """The resolved permission posture for one chat runtime."""

    role: ChatRole
    approvals: Approvals
    conduct: str


def resolve_posture(role: ChatRole, *, planning_revision: bool = False) -> ChatPosture:
    """Return the posture a chat runs under.

    Preconditions: ``planning_revision`` is true only once the Planning
    manifest reports a materialized plan, so the documents to author exist.
    Postconditions: the returned posture is provider-agnostic; translation
    happens in ``provider_options``.
    """
    if role is ChatRole.ADVISORY:
        return ChatPosture(role=role, approvals=Approvals.ALWAYS, conduct=ADVISORY_CONDUCT)
    if role is ChatRole.PLANNING and planning_revision:
        # Writes are already scoped to the plan artifact root, so prompting
        # per edit would be noise: authoring plan documents is the whole job.
        return ChatPosture(role=role, approvals=Approvals.ON_REQUEST, conduct=PLANNING_CONDUCT)
    return ChatPosture(role=role, approvals=Approvals.INHERIT, conduct="")


def provider_options(
    provider: Provider, posture: ChatPosture, options: dict[str, Any]
) -> dict[str, Any]:
    """Translate a posture into provider-facing option overrides.

    Preconditions: ``options`` holds provider knobs only; Atelier metadata
    keys are stripped by the caller.
    Postconditions: unrelated options are preserved.

    Never selects a plan / read-only posture. Plan mode *ends* by calling
    a plan-exit tool that arrives as a permission request, so a chat held
    there always produces one prompt it cannot satisfy and the turn blocks
    on the unanswered ACP call.
    """
    next_options = dict(options)
    if posture.approvals is Approvals.INHERIT:
        return next_options
    prompt_always = posture.approvals is Approvals.ALWAYS
    if provider == "amp":
        next_options["permission_mode"] = "default"
        next_options.pop("read_only", None)
    elif provider == "codex":
        next_options["sandbox"] = "workspace-write"
        next_options["approval_mode"] = "on-request" if prompt_always else "on-failure"
    elif provider in {"claude-code", "claude-acp"}:
        next_options["permission_mode"] = "default" if prompt_always else "acceptEdits"
    elif provider == "codex-acp":
        next_options["mode"] = "agent"
    elif provider == "opencode":
        next_options["mode"] = "build"
    return next_options


__all__ = [
    "ADVISORY_CONDUCT",
    "PLANNING_CONDUCT",
    "Approvals",
    "ChatPosture",
    "ChatRole",
    "provider_options",
    "resolve_posture",
]
