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

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from src.domain.models import Artifact, ArtifactType
from src.domain.workstore.dtos import RecordArtifactRequest
from src.domain.workstore.ports import WorkStore

# Per-type allowed status values. Free-text would bypass the visual-
# vocabulary contract: the FE renders these as typed status pills.
_PR_STATUSES = frozenset({"open", "draft", "merged", "closed"})
_JIRA_STATUSES = frozenset({"todo", "in_progress", "in_review", "done", "blocked"})
_DOC_STATUSES = frozenset({"draft", "published"})

_ALLOWED_TYPES: frozenset[str] = frozenset({"pr", "jira", "doc"})


class InvalidMarker(ValueError):
    """The payload doesn't satisfy the per-type schema."""


def record_artifact(
    work_slug: str,
    agent_slug: str,
    payload: dict[str, Any],
    *,
    workstore: WorkStore,
    resolve_workdir: Callable[[str, str], Path],
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
    workdir = resolve_workdir(work_slug, agent_slug).resolve()
    candidate = (workdir / rel_path).resolve()
    # Reject paths that escape the agent's worktree (../../etc/passwd).
    try:
        candidate.relative_to(workdir)
    except ValueError as exc:
        raise InvalidMarker(
            f"doc path escapes the agent's worktree: {rel_path}"
        ) from exc
    if not candidate.is_file():
        raise InvalidMarker(f"doc path does not exist: {rel_path}")
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
