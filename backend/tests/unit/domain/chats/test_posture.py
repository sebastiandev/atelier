"""Chat postures are declared by the owner, never inferred by the chat.

The deadlock these guard against: a plan / read-only posture ends by
calling a plan-exit tool that arrives as a permission request, so a chat
held there always produces one prompt it cannot satisfy and the turn
blocks forever on the unanswered ACP call.
"""

import pytest

from src.domain.chats.posture import (
    Approvals,
    ChatRole,
    provider_options,
    resolve_posture,
)
from src.domain.models import Provider

PROVIDERS: tuple[Provider, ...] = (
    "claude-code",
    "claude-acp",
    "codex",
    "codex-acp",
    "opencode",
    "amp",
)

# Per provider, the option value that cannot answer its own plan-exit prompt.
BLOCKING: dict[Provider, tuple[str, str]] = {
    "claude-code": ("permission_mode", "plan"),
    "claude-acp": ("permission_mode", "plan"),
    "codex": ("sandbox", "read-only"),
    "codex-acp": ("mode", "read-only"),
    "opencode": ("mode", "plan"),
    "amp": ("read_only", "true"),
}

ACTING_ROLES = (
    (ChatRole.ADVISORY, False),
    (ChatRole.PLANNING, True),
)


def test_advisory_prompts_before_every_action() -> None:
    assert resolve_posture(ChatRole.ADVISORY).approvals is Approvals.ALWAYS


def test_advisory_forbids_implementation() -> None:
    assert "not allowed to implement" in resolve_posture(ChatRole.ADVISORY).conduct


def test_advisory_allows_temporary_files() -> None:
    assert "temporary plans or investigations" in resolve_posture(
        ChatRole.ADVISORY
    ).conduct


def test_planning_authors_documents_without_prompting_per_edit() -> None:
    posture = resolve_posture(ChatRole.PLANNING, planning_revision=True)

    assert posture.approvals is Approvals.ON_REQUEST


def test_planning_before_materialization_keeps_creator_options() -> None:
    """Discovery has no plan documents to author yet."""
    posture = resolve_posture(ChatRole.PLANNING, planning_revision=False)

    assert posture.approvals is Approvals.INHERIT


def test_explore_keeps_creator_options() -> None:
    assert resolve_posture(ChatRole.EXPLORE).approvals is Approvals.INHERIT


def test_explore_adds_no_conduct() -> None:
    assert resolve_posture(ChatRole.EXPLORE).conduct == ""


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(("role", "revision"), ACTING_ROLES)
def test_acting_roles_never_select_a_blocking_posture(
    provider: Provider, role: ChatRole, revision: bool
) -> None:
    posture = resolve_posture(role, planning_revision=revision)

    options = provider_options(provider, posture, {})

    key, blocking = BLOCKING[provider]
    assert options.get(key) != blocking


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(("role", "revision"), ACTING_ROLES)
def test_acting_roles_preserve_unrelated_options(
    provider: Provider, role: ChatRole, revision: bool
) -> None:
    posture = resolve_posture(role, planning_revision=revision)

    options = provider_options(provider, posture, {"thinking_effort": "medium"})

    assert options["thinking_effort"] == "medium"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_explore_leaves_every_option_untouched(provider: Provider) -> None:
    posture = resolve_posture(ChatRole.EXPLORE)
    original = {"permission_mode": "plan", "mode": "read-only"}

    assert provider_options(provider, posture, original) == original


def test_advisory_clears_an_inherited_amp_read_only_flag() -> None:
    posture = resolve_posture(ChatRole.ADVISORY)

    assert "read_only" not in provider_options("amp", posture, {"read_only": "true"})


@pytest.mark.parametrize("provider", ("claude-code", "claude-acp"))
def test_planning_revision_accepts_edits_inside_its_roots(provider: Provider) -> None:
    """Writes are already scoped to the plan root, so per-edit prompts are noise."""
    posture = resolve_posture(ChatRole.PLANNING, planning_revision=True)

    options = provider_options(provider, posture, {})

    assert options["permission_mode"] == "acceptEdits"


@pytest.mark.parametrize("provider", ("claude-code", "claude-acp"))
def test_advisory_still_prompts_per_edit(provider: Provider) -> None:
    posture = resolve_posture(ChatRole.ADVISORY)

    options = provider_options(provider, posture, {})

    assert options["permission_mode"] == "default"

