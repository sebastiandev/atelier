"""Per-type artifact status vocabularies + validation.

Each artifact type has its own lifecycle, so a single status enum
doesn't fit. Instead we expose three frozensets and a dispatcher.

  - ``PR_STATUSES``: draft/open/merged/closed.
  - ``DOC_STATUSES``: draft/pending/committed. ``draft`` is the canonical
    state for shared-folder docs; ``pending``/``committed`` are derived
    from git for worktree-resident docs (see ``list_for_work``).
  - ``JIRA_STATUSES``: todo/in_progress/in_review/done/closed/blocked.

``validate_status(type, value)`` is the single entry point the recorder
calls — raises ``InvalidStatus`` on a value that doesn't belong to the
type. Adding a new artifact type means adding a frozenset and a branch
here.
"""

from __future__ import annotations

PR_STATUSES = frozenset({"draft", "open", "merged", "closed"})
DOC_STATUSES = frozenset({"draft", "pending", "committed"})
JIRA_STATUSES = frozenset(
    {"todo", "in_progress", "in_review", "done", "closed", "blocked"}
)

_BY_TYPE: dict[str, frozenset[str]] = {
    "pr": PR_STATUSES,
    "doc": DOC_STATUSES,
    "jira": JIRA_STATUSES,
}


class InvalidStatus(ValueError):
    """Status value isn't part of the type's allowed set."""


def validate_status(artifact_type: str, status: str) -> None:
    """Raise ``InvalidStatus`` if ``status`` doesn't belong to the
    type's allowed vocabulary. Unknown types raise too — adding a new
    artifact type means registering it here first."""
    allowed = _BY_TYPE.get(artifact_type)
    if allowed is None:
        raise InvalidStatus(f"unknown artifact type: {artifact_type!r}")
    if status not in allowed:
        raise InvalidStatus(
            f"invalid {artifact_type!r} status {status!r} "
            f"(expected one of {sorted(allowed)})"
        )


__all__ = [
    "DOC_STATUSES",
    "InvalidStatus",
    "JIRA_STATUSES",
    "PR_STATUSES",
    "validate_status",
]
