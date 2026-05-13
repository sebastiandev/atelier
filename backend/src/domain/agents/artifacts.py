"""ArtifactTracker — validate and persist an agent's artifact marker.

An agent expresses intent to record an artifact in two ways:

  1. Tool call: ``atelier__record_pr`` / ``record_jira`` / ``record_doc``,
     registered via each adapter's tool-extension mechanism (in-process
     MCP for Claude, subprocess MCP for Amp). Schema-enforced by the SDK.
  2. Text marker: a JSON line ``{"atelier_artifact": {...}}`` scanned out
     of agent output. Fallback for adapters without tool registration
     and a safety net when the model emits the right idea in the wrong
     form.

Both paths land in the supervisor as an ``ArtifactMarker`` event whose
payload is the per-type body. This module owns the validation +
persistence that follows.

Attribution invariant: ``work_slug`` and ``agent_slug`` are supplied by
the supervisor from its own state and never read from the payload — the
agent cannot forge attribution to a different agent.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from src.domain.artifacts import (
    DOC_STATUSES,
    JIRA_STATUSES,
    PR_STATUSES,
    Artifact,
)
from src.domain.models import ArtifactType
from src.domain.workstore.dtos import RecordArtifactRequest
from src.domain.workstore.ports import WorkStore

# How long to wait for a doc-type artifact's file to appear on disk
# before failing validation. Claude can emit Write and record_doc as
# parallel tool uses in the same assistant turn — the Write executes
# milliseconds after we see the record_doc tool use, so the tracker
# would otherwise reject paths that are about to exist. 500ms covers
# the long tail; the success case returns immediately.
_DOC_PATH_WAIT_SECONDS = 0.5
_DOC_PATH_POLL_INTERVAL = 0.05

# Per-type allowed status values live in ``src.domain.artifacts.status``
# and are shared with the validation layer. Aliased here so the
# ``_require_status`` calls below stay readable.
_PR_STATUSES = PR_STATUSES
_JIRA_STATUSES = JIRA_STATUSES
_DOC_STATUSES = DOC_STATUSES

_ALLOWED_TYPES: frozenset[str] = frozenset({"pr", "jira", "doc"})


class InvalidMarker(ValueError):
    """The payload doesn't satisfy the per-type schema."""


def record_artifact(
    work_slug: str,
    agent_slug: str,
    payload: dict[str, Any],
    *,
    workstore: WorkStore,
    resolve_allowed_roots: Callable[[str, str], list[Path]],
) -> Artifact:
    artifact_type = _require_type(payload)
    title = _require_str(payload, "title")

    if artifact_type == "pr":
        return workstore.record_artifact(
            RecordArtifactRequest(
                work_slug=work_slug,
                agent_slug=agent_slug,
                type="pr",
                title=title,
                status=_require_status(payload, _PR_STATUSES, default="open"),
                url=_require_str(payload, "url"),
                repo=_optional_str(payload, "repo"),
            )
        )

    if artifact_type == "jira":
        return workstore.record_artifact(
            RecordArtifactRequest(
                work_slug=work_slug,
                agent_slug=agent_slug,
                type="jira",
                title=title,
                status=_require_status(payload, _JIRA_STATUSES),
                url=_require_str(payload, "url"),
            )
        )

    rel_path = _require_str(payload, "path")
    roots = resolve_allowed_roots(work_slug, agent_slug)
    if not roots:
        raise InvalidMarker(
            "no allowed roots for this agent — cannot resolve doc path"
        )
    # Relative paths resolve against the first root (the agent's
    # worktree). Absolute paths are accepted as-is so an agent can
    # point at a shared folder via the symlink under ./shares/<mount>/
    # OR directly via the canonical path.
    primary = roots[0].resolve()
    candidate = Path(rel_path)
    candidate = (primary / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    # Accept the path if its resolved real path falls inside ANY allowed
    # root — worktree or one of the project's shared folders. Rejecting
    # ``../../etc/passwd`` still works because /etc isn't a registered
    # root.
    real_roots = [r.resolve() for r in roots]
    if not any(_is_inside(candidate, root) for root in real_roots):
        raise InvalidMarker(
            f"doc path escapes the agent's allowed roots: {rel_path}"
        )
    if not _wait_for_file(candidate, _DOC_PATH_WAIT_SECONDS):
        # Phrase the error so a re-reading agent self-corrects on its
        # next turn: spell out the contract (file must exist before
        # the marker), so the obvious recovery is "Write the file,
        # then call record_doc again".
        raise InvalidMarker(
            f"doc path does not exist at {rel_path!r}. "
            "record_doc requires the file to already be on disk in "
            "your working directory — call Write/Edit to create it "
            "first, then re-emit this marker."
        )
    return workstore.record_artifact(
        RecordArtifactRequest(
            work_slug=work_slug,
            agent_slug=agent_slug,
            type="doc",
            title=title,
            status=_require_status(payload, _DOC_STATUSES, default="draft"),
            doc_path=str(candidate),
        )
    )


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _wait_for_file(path: Path, timeout_seconds: float) -> bool:
    """Poll for ``path`` to exist as a file, up to ``timeout_seconds``.

    Returns True the moment the file appears (typically the first poll),
    False if the deadline elapses. Used by the doc-type tracker because
    Claude can emit Write and record_doc as parallel tool uses in the
    same turn — the file write completes a beat after our validation
    runs, and rejecting on the first miss would cause spurious errors.

    Sync + sleeps; the supervisor calls the tracker via
    ``asyncio.to_thread`` so the event loop stays responsive.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        if path.is_file():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_DOC_PATH_POLL_INTERVAL)


def _require_type(payload: dict[str, Any]) -> ArtifactType:
    raw = payload.get("type")
    if not isinstance(raw, str) or raw not in _ALLOWED_TYPES:
        raise InvalidMarker(
            f"missing or unknown artifact type: {raw!r} "
            f"(expected one of {sorted(_ALLOWED_TYPES)})"
        )
    return cast(ArtifactType, raw)


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidMarker(f"missing or empty {key!r}")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidMarker(f"{key!r} must be a non-empty string when provided")
    return value


def _require_status(
    payload: dict[str, Any],
    allowed: frozenset[str],
    *,
    default: str | None = None,
) -> str:
    raw = payload.get("status")
    if raw is None:
        if default is None:
            raise InvalidMarker(
                f"missing 'status' (expected one of {sorted(allowed)})"
            )
        return default
    if not isinstance(raw, str) or raw not in allowed:
        raise InvalidMarker(
            f"invalid status {raw!r} (expected one of {sorted(allowed)})"
        )
    return raw


__all__ = ["InvalidMarker", "record_artifact"]
