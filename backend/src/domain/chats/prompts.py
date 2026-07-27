"""Prompt builders for regular exploratory chats."""

from dataclasses import dataclass
from pathlib import Path

from src.domain.prompts import build_prompt


@dataclass(frozen=True)
class RegularChatRuntimePrompt:
    """Inputs needed to render a regular chat runtime prompt."""

    chat_slug: str
    title: str
    workdir: Path
    working_label: str
    working_details: str
    link_label: str
    link_details: str
    discussion_only: bool = False
    context_seed: str | None = None


@build_prompt.register
def _(req: RegularChatRuntimePrompt) -> str:
    """Render the system prompt for a normal exploratory chat."""
    posture = (
        "This is a discussion about an Atelier run, not an implementation "
        "session. Answer questions and help the user evaluate the run; the "
        "loop remains authoritative and owns every change to the codebase.\n"
        "Do not implement fixes: no edits to project source, tests, or "
        "configuration, and no commits, even if the user asks. When you have "
        "identified a change, describe it and let the user route it back to "
        "the loop.\n"
        "You may read anything in the workspace, and you may write scratch "
        "files -- scratch analysis, a draft note, a throwaway reproduction -- "
        "when they help answer the question. Keep them clearly temporary and "
        "outside the project's tracked sources.\n\n"
        if req.discussion_only
        else ""
    )
    seed = (
        "Selected run-stage context:\n<run_stage_context>\n"
        f"{req.context_seed}\n"
        "</run_stage_context>\n\n"
        if req.context_seed
        else ""
    )
    return (
        "You are an Atelier exploratory chat.\n"
        f"Chat: {req.chat_slug} - {req.title}\n"
        "Purpose: help the user think through ambiguity, compare options, "
        "identify risks, and shape possible next actions before work is "
        "handed to an agent.\n\n"
        f"Working directory: {req.workdir}\n"
        f"Working folder: {req.working_label}\n"
        f"{req.working_details}\n\n"
        f"Linked to: {req.link_label}\n"
        f"{req.link_details}\n\n"
        f"{posture}"
        f"{seed}"
        "This is not a tracked implementation agent. Do not claim that a "
        "worktree, pull request, or artifact was created from this chat. "
        "When the conversation becomes executable, summarize the recommended "
        "next action clearly so the user can hand it off to an agent."
    )


__all__ = ["RegularChatRuntimePrompt"]
