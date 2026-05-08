"""System-prompt rendering — Atelier-level concern, provider-agnostic.

The persona/role pair is an Atelier abstraction; providers don't know
about it. The route renders it into a system_prompt string that's
folded into ``CommonAgentConfig`` before the spec layer runs.

Walking-skeleton template — intentionally minimal. Persona-specific
prompt engineering is its own future story.
"""

from src.domain.models import Persona

# Marker convention taught to every launched agent. Two paths land in the
# same supervisor pipeline:
#   1. ``atelier__record_pr`` / ``record_jira`` / ``record_doc`` tools
#      (registered via the adapter's tool-extension mechanism — primary,
#      schema-enforced).
#   2. ``{"atelier_artifact": {...}}`` JSON line in plain output (fallback).
# Status enums match ``domain/agents/artifacts.py``.
_ARTIFACT_MARKER_GUIDE = """\
Recording artifacts
-------------------
When you produce a tracked artifact (a pull request, a Jira ticket, or a
brand-new document) record it so it shows up on this work's artifact rail.

Prefer the dedicated tool when it's available:
  - atelier__record_pr(url, title, status, repo?)
      status ∈ open | draft | merged | closed (default: open)
  - atelier__record_jira(url, title, status)
      status ∈ todo | in_progress | in_review | done | blocked
  - atelier__record_doc(path, title, status?)
      path: relative to your working directory; the file must already exist
      status ∈ draft | published (default: draft)
      Only call this for documents you AUTHORED in this turn (a new file
      you just wrote with Write/Edit). Don't record edits to existing
      files, and don't record code files — only standalone documents
      like design notes, ADRs, READMEs, proposals.

If those tools aren't registered, emit a single JSON line on its own:
  {"atelier_artifact": {"type": "pr", "url": "...", "title": "...", "status": "open"}}
  {"atelier_artifact": {"type": "jira", "url": "...", "title": "...", "status": "in_progress"}}
  {"atelier_artifact": {"type": "doc", "path": "docs/design.md", "title": "...", "status": "draft"}}

Only emit a marker once you've actually created the artifact (e.g. after
``gh pr create`` returned a URL, or right after Write created a new doc).
Atelier ignores any agent identifier in the payload — attribution is
stamped by the supervisor."""


def render_system_prompt(persona: Persona, role: str) -> str:
    return (
        f"You are an Atelier {persona} agent.\n"
        f"Role: {role}.\n"
        f"Stay in character and focus on the work assigned to you.\n\n"
        f"{_ARTIFACT_MARKER_GUIDE}"
    )


__all__ = ["render_system_prompt"]
