"""System-prompt rendering — Atelier-level concern, provider-agnostic.

The persona/role pair is an Atelier abstraction; providers don't know
about it. The route renders it into a system_prompt string that's
folded into ``CommonAgentConfig`` before the spec layer runs.

Walking-skeleton template — intentionally minimal. Persona-specific
prompt engineering is its own future story.
"""

from src.domain.models import Persona


def render_system_prompt(persona: Persona, role: str) -> str:
    return (
        f"You are an Atelier {persona} agent.\n"
        f"Role: {role}.\n"
        f"Stay in character and focus on the work assigned to you."
    )


__all__ = ["render_system_prompt"]
