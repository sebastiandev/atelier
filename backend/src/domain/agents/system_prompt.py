"""System-prompt rendering — Atelier-level concern, provider-agnostic.

The persona/role pair is an Atelier abstraction; providers don't know
about it. The route renders it into a system_prompt string that's
folded into ``CommonAgentConfig`` before the spec layer runs.

Walking-skeleton template — intentionally minimal. Persona-specific
prompt engineering is its own future story.
"""

from collections.abc import Sequence
from pathlib import Path

from src.domain.models import Persona
from src.domain.sharedfolders.dtos import ShareSummary

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
      you just wrote — via Write, Edit, create_file, apply_patch, or
      whatever file-authoring tool your client surfaces). Don't record
      edits to existing files, and don't record code files — only
      standalone documents like design notes, ADRs, READMEs, plans,
      stories, proposals.

If those tools aren't registered OR a tool call doesn't get through,
emit a single JSON line on its own (one line per artifact, flush-left
or lightly indented, not inside a code fence):
  {"atelier_artifact": {"type": "pr", "url": "...", "title": "...", "status": "open"}}
  {"atelier_artifact": {"type": "jira", "url": "...", "title": "...", "status": "in_progress"}}
  {"atelier_artifact": {"type": "doc", "path": "docs/design.md", "title": "...", "status": "draft"}}

Only emit a marker once you've actually created the artifact (e.g. after
``gh pr create`` returned a URL, or right after the file landed on
disk). Atelier ignores any agent identifier in the payload — attribution
is stamped by the supervisor."""


def detect_shared_envs(workdir: Path | None) -> list[str]:
    """Return relative paths of `.venv` / `venv` / `node_modules`
    symlinks present in the worktree.

    Worktrees inherit these symlinks from the source repo (see
    ``infrastructure/git/worktree_manager._symlink_devtime_artifacts``)
    so sibling agents share one Python env / one node_modules tree.
    The list drives a system-prompt nudge warning agents about
    concurrent ``uv add`` / ``pip install`` / ``npm install`` races
    across siblings — they touch the same lockfiles and partial state.

    Mirrors the symlink walk in the worktree manager: top-level first,
    then one level deep so monorepo layouts (``backend/.venv``,
    ``frontend/node_modules``) are picked up.
    """
    if workdir is None:
        return []
    names = (".venv", "venv", "node_modules")
    found: list[str] = []
    for name in names:
        candidate = workdir / name
        if candidate.is_symlink():
            found.append(name)
    try:
        children = sorted(p for p in workdir.iterdir() if p.is_dir())
    except OSError:
        return found
    for child in children:
        if child.is_symlink():
            continue
        for name in names:
            candidate = child / name
            if candidate.is_symlink():
                found.append(f"{child.name}/{name}")
    return found


def render_system_prompt(
    persona: Persona,
    role: str,
    *,
    workdir: Path | None = None,
    shares: Sequence[ShareSummary] = (),
    is_detached_worktree: bool = False,
    shared_envs: Sequence[str] = (),
) -> str:
    # Telling the agent its working directory explicitly is load-bearing:
    # without it, models routinely write files to $HOME (or wherever they
    # default) instead of the workdir, then pass a relative path to
    # record_doc that the tracker resolves against a different location.
    # The CLI may inject some env info too, but this line is the
    # authoritative source for "where am I working".
    workdir_block = (
        f"Working directory: {workdir}\n"
        f"Create and edit files inside this directory; ALL paths you "
        f"pass to tools (Write, Edit, atelier__record_doc, etc.) should "
        f"be relative to this directory unless the task explicitly tells "
        f"you to use an absolute path.\n\n"
        if workdir is not None
        else ""
    )
    detached_block = _DETACHED_WORKTREE_GUIDE if is_detached_worktree else ""
    shared_envs_block = _render_shared_envs_block(shared_envs)
    return (
        f"You are an Atelier {persona} agent.\n"
        f"Role: {role}.\n"
        f"Stay in character and focus on the work assigned to you.\n\n"
        f"{workdir_block}"
        f"{detached_block}"
        f"{shared_envs_block}"
        f"{_render_shares_block(shares)}"
        f"{_ARTIFACT_MARKER_GUIDE}"
    )


# Injected when the agent's worktree is in detached HEAD. The risk we're
# warning about is narrow but real: commits stay reachable as long as
# HEAD points at them, but if the agent runs ``git checkout`` /
# ``git switch`` to another branch before creating one from the current
# HEAD, the committed work becomes orphaned (reflog GC after ~30-90d).
# Creating a branch with ``git switch -c <name>`` anchors the work to a
# real ref and is safe.
_DETACHED_WORKTREE_GUIDE = """\
Detached HEAD worktree
----------------------
This worktree starts in detached HEAD with no branch. You can edit and
commit normally; commits stay reachable from HEAD as long as you don't
move it.

Before pushing, propose a branch name and run:
  git switch -c <branch-name>

Do NOT ``git checkout`` / ``git switch`` to a different existing branch
without first creating a branch from the current HEAD — doing so would
orphan any commits made in this worktree. If the task asks you to switch
branches, ask the user first or save the work with ``git switch -c``.

"""



def _render_shared_envs_block(shared_envs: Sequence[str]) -> str:
    """Warn the agent that its dev environment is shared with siblings.

    Atelier symlinks ``.venv`` / ``node_modules`` from the source repo
    into every worktree so sibling agents don't each download multi-GB
    dependency trees. The side effect: concurrent installs from
    different worktrees race on the same lockfile + ``site-packages`` /
    ``node_modules`` state. ``uv add`` and ``pip install`` can corrupt
    the env half-way through.
    """
    if not shared_envs:
        return ""
    bullets = "\n".join(f"  - {name}" for name in shared_envs)
    return (
        "Shared dev environment\n"
        "----------------------\n"
        "The following dependency directories are symlinks to the "
        "source repo — they are shared with every sibling agent on "
        "this work:\n"
        f"{bullets}\n"
        "If you install dependencies (`uv add`, `pip install`, "
        "`npm install`, etc.) they affect every sibling immediately, "
        "and concurrent installs race on lockfiles + partial state. "
        "Before installing: (1) tell the user what you're about to "
        "add, (2) make sure no sibling agent is mid-install, (3) "
        "prefer adding to the source repo's manifest and re-resolving "
        "rather than ad-hoc installs.\n\n"
    )


def _render_shares_block(shares: Sequence[ShareSummary]) -> str:
    """Tell the agent which shared folders exist inside its worktree and
    what the contract is. Omitted when no shares — keeps the prompt
    quiet for projects that don't use them."""
    if not shares:
        return ""
    lines = ["Shared folders (persistent across agents in this project,"]
    lines.append("edited concurrently — last writer wins):")
    for share in shares:
        lines.append(f'  - "{share.name}" at ./{share.mount_path}/')
    lines.append(
        "These paths are symlinks into shared storage; edits propagate "
        "live to every agent that has the same share mounted. Don't "
        "stomp other agents' in-flight edits."
    )
    return "\n".join(lines) + "\n\n"


__all__ = ["render_system_prompt"]
