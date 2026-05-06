"""Render an agent's contexts into per-source files plus an index.

Self-contained types (``text`` / ``url`` / ``file`` / ``agentout``) are
rendered inline. Connection-backed types (``jira`` / ``sentry`` /
``honeycomb``) are not fetched here — the caller pre-fetches at the
boundary (``start``) and threads the resolved bodies in via
``fetched_bodies``. This split keeps the renderer pure and makes the
fetch failure-mode (halt agent start) trivial to enforce: a missing
body for a connection-backed context is a programmer error, not a
runtime fallback.

Output layout under the agent's metadata dir (``<workspace_root>/works/<work>/agents/<agent>/``)::

    agent.json
    transcript.ndjson
    context.md          ← this index
    context/
        text-1.md
        url-1.md
        jira-ENG-3421.md

The supervisor injects a single first-message at agent start that points the
LLM at ``context.md``; the agent reads individual files via its own tools as
needed.
"""

from __future__ import annotations

import re

from src.domain.models import Context
from src.domain.workstore.ports import WorkspaceFiles

_FILENAME_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")
# Types whose value is a structured external ID (e.g. ``ENG-3421``) and
# is worth using verbatim in the filename when it parses as a slug.
# Other types (``text``, ``url``, ``file``, ``agentout``) always fall
# through to numbered filenames — their values are arbitrary content.
_SLUGGABLE_TYPES = frozenset({"jira", "sentry", "honeycomb"})
_INDEX_INTRO = (
    "# Context for this task\n\n"
    "Read these as needed; they are not loaded into the conversation by default.\n"
)

_TYPE_HEADINGS: dict[str, str] = {
    "text": "Text",
    "url": "URLs",
    "file": "Files",
    "jira": "Jira tickets",
    "sentry": "Sentry issues",
    "honeycomb": "Honeycomb queries",
    "agentout": "Other agents",
}


def render_agent_contexts(
    files: WorkspaceFiles,
    work_slug: str,
    agent_slug: str,
    contexts: list[Context],
    fetched_bodies: dict[int, str] | None = None,
    *,
    since_index: int = 0,
) -> str | None:
    """Write per-source files for ``contexts[since_index:]`` and (re)build
    the index from the full ``contexts`` list. Returns the absolute path
    to the index file, or ``None`` when ``contexts`` is empty.

    ``fetched_bodies`` is keyed by absolute index into ``contexts``; the
    boundary pre-fetches connection-backed entries and supplies them
    here. For ``since_index > 0`` only the new entries (those past the
    cursor) need bodies — pre-existing connection-backed files on disk
    are kept as-is, so a Jira ticket ingested at start doesn't get
    re-fetched on every later add (the original snapshot stays stable).

    A connection-backed context with no entry in ``fetched_bodies`` AND
    index >= ``since_index`` raises — that's a wiring bug, not a user
    error.
    """
    if not contexts:
        return None

    fetched = fetched_bodies or {}
    filenames = derive_filenames(contexts)
    for idx in range(since_index, len(contexts)):
        c = contexts[idx]
        files.write_agent_context_file(
            work_slug, agent_slug, filenames[idx], _body_for(c, fetched.get(idx))
        )
    entries = list(zip(filenames, contexts, strict=True))
    return files.write_agent_context_index(work_slug, agent_slug, _build_index(entries))


def derive_filenames(contexts: list[Context]) -> list[str]:
    """Map each context to its on-disk filename, preserving order. The
    filename is the same one ``render_agent_contexts`` would write — so
    callers that want to reference newly-added entries (e.g. in the
    auto-prepend hint sent alongside a user message) can derive them
    without walking the filesystem."""
    taken: set[str] = set()
    counters: dict[str, int] = {}
    return [_filename_for(c, taken, counters) for c in contexts]


def _filename_for(c: Context, taken: set[str], counters: dict[str, int]) -> str:
    if (
        c.type in _SLUGGABLE_TYPES
        and _FILENAME_SAFE.fullmatch(c.value)
        and len(c.value) <= 64
    ):
        candidate = f"{c.type}-{c.value}.md"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    counters[c.type] = counters.get(c.type, 0) + 1
    candidate = f"{c.type}-{counters[c.type]}.md"
    while candidate in taken:
        counters[c.type] += 1
        candidate = f"{c.type}-{counters[c.type]}.md"
    taken.add(candidate)
    return candidate


def _body_for(c: Context, fetched: str | None) -> str:
    if c.type == "text":
        return c.value if c.value.endswith("\n") else c.value + "\n"
    if c.type == "url":
        return f"# URL\n\n<{c.value}>\n\nFetch with the WebFetch tool when needed.\n"
    if c.type == "file":
        return f"# File\n\nPath: `{c.value}`\n\nRead with the Read tool when needed.\n"
    if c.type == "agentout":
        return f"# Agent output\n\nAgent: `{c.value}`\n"
    if fetched is None:
        raise RuntimeError(
            f"connection-backed context type {c.type!r} requires a pre-fetched body"
        )
    return fetched if fetched.endswith("\n") else fetched + "\n"


def _build_index(entries: list[tuple[str, Context]]) -> str:
    grouped: dict[str, list[tuple[str, Context]]] = {}
    for filename, c in entries:
        grouped.setdefault(c.type, []).append((filename, c))

    parts: list[str] = [_INDEX_INTRO]
    for ctype, items in grouped.items():
        heading = _TYPE_HEADINGS.get(ctype, ctype.capitalize())
        parts.append(f"\n## {heading}\n")
        for filename, c in items:
            parts.append(f"- [{filename}](context/{filename}){_summary(c)}")
    return "\n".join(parts).rstrip() + "\n"


def _summary(c: Context) -> str:
    if c.type == "text":
        first_line = next((line for line in c.value.splitlines() if line.strip()), "")
        if len(first_line) > 80:
            first_line = first_line[:77] + "…"
        return f" — {first_line}" if first_line else ""
    return f" — {c.value}"


__all__ = ["derive_filenames", "render_agent_contexts"]
