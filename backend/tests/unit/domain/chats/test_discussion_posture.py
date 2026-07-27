"""Run-discussion chats prompt for every action instead of going read-only.

A hard read-only / plan posture deadlocks: plan mode ends by calling the
plan-exit tool, which arrives as a permission request, so the discussion
always produced one prompt it could never satisfy and the turn blocked on
the unanswered ACP call forever.
"""

import pytest

from src.domain.chats.prompts import RegularChatRuntimePrompt, build_prompt
from src.domain.chats.runtime import _discussion_options
from src.domain.models import Provider

PROVIDERS: tuple[Provider, ...] = (
    "claude-code",
    "claude-acp",
    "codex",
    "codex-acp",
    "opencode",
    "amp",
)

# The option value that would put each provider into a posture where it
# cannot write and must ask permission to leave planning.
DEADLOCKING = {
    "claude-code": ("permission_mode", "plan"),
    "claude-acp": ("permission_mode", "plan"),
    "codex": ("sandbox", "read-only"),
    "codex-acp": ("mode", "read-only"),
    "opencode": ("mode", "plan"),
}


@pytest.mark.parametrize("provider", PROVIDERS)
def test_discussion_never_selects_a_blocking_posture(provider: Provider) -> None:
    options = _discussion_options(provider, {})

    key, blocking = DEADLOCKING.get(provider, ("permission_mode", "plan"))
    assert options.get(key) != blocking


@pytest.mark.parametrize("provider", PROVIDERS)
def test_discussion_can_write_scratch_files(provider: Provider) -> None:
    options = _discussion_options(provider, {})

    assert options.get("read_only") != "true"
    assert options.get("sandbox") != "read-only"
    assert options.get("mode") != "read-only"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_discussion_preserves_unrelated_options(provider: Provider) -> None:
    options = _discussion_options(provider, {"thinking_effort": "medium"})

    assert options["thinking_effort"] == "medium"


def test_discussion_clears_an_inherited_amp_read_only_flag() -> None:
    """Amp stage agents may carry read_only; a discussion must drop it."""
    options = _discussion_options("amp", {"read_only": "true"})

    assert "read_only" not in options


def _prompt(*, discussion_only: bool) -> str:
    return build_prompt(
        RegularChatRuntimePrompt(
            chat_slug="CHT-001",
            title="Code review",
            workdir="/tmp/work",
            working_label="worktree",
            working_details="details",
            link_label="WRK-002",
            link_details="details",
            discussion_only=discussion_only,
        )
    )


def test_discussion_prompt_forbids_implementation() -> None:
    assert "Do not implement fixes" in _prompt(discussion_only=True)


def test_discussion_prompt_allows_scratch_files() -> None:
    assert "scratch files" in _prompt(discussion_only=True)


def test_regular_chat_prompt_omits_the_discussion_posture() -> None:
    assert "Do not implement fixes" not in _prompt(discussion_only=False)
