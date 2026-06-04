"""List a work's artifacts with the per-type enrichment the FE needs.

Replaces the old thin "fetch + return" version. Owns the cross-cutting
work the HTTP route used to do inline:

  - resolve ``agent_id → agent_slug`` for attribution display,
  - for ``DocArtifact``, classify the location (worktree vs shared)
    and derive the status from observed filesystem state — shared docs
    are always ``draft``; worktree docs are ``committed`` when the
    file matches HEAD and ``pending`` otherwise. The persisted
    ``status`` column isn't read for docs; the derived value is the
    served value.
  - build a typed ``ArtifactView`` per row, type-narrowed so the
    serialization layer can format each kind without reaching for the
    other types' fields.

Path-resolution dependencies are passed as callables so this stays out
of the infrastructure import graph — the route binds the real
``WorkspacePaths`` / sharestore behind the seam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import singledispatch
from pathlib import Path

from src.domain.agents.doc_state import (
    LocationKind,
    classify_location,
    git_state,
)
from src.domain.artifacts import (
    ArtifactType,
    DocArtifact,
    DocStatus,
    JiraArtifact,
    PrArtifact,
)
from src.domain.workstore.ports import WorkStore

# Resolves an agent's worktree path. ``None`` when the agent has no
# worktree on disk (deleted, non-git source, etc.) — callers fall back
# to "no worktree binding" for ``classify_location``.
WorktreeResolver = Callable[[str, str], Path | None]

# Returns the resolved real-path roots of every share registered on a
# project. Used as the input to ``classify_location`` so docs that live
# under a share canonical *or* a custom real_path both classify as
# shared regardless of how they were addressed.
ShareRootResolver = Callable[[str], list[Path]]


@dataclass(frozen=True, kw_only=True)
class ArtifactView:
    """Read-side projection. Carries the served fields the FE renders.

    ``status`` is the derived value for docs (location- + git-aware)
    and the persisted value for PR/Jira. ``location_kind`` is set only
    on docs.
    """

    slug: str
    type: ArtifactType
    title: str
    status: str
    created_at: datetime
    agent_slug: str | None
    url: str | None
    repo: str | None
    doc_path: str | None
    location_kind: LocationKind | None


def execute(
    *,
    workstore: WorkStore,
    work_slug: str,
    resolve_worktree: WorktreeResolver,
    resolve_share_roots: ShareRootResolver,
) -> list[ArtifactView]:
    """Build the per-row view list. Raises ``ValueError`` upstream (from
    ``workstore.list_artifacts_for_work``) when the work slug doesn't
    resolve; callers map to 404."""
    artifacts = workstore.list_artifacts_for_work(work_slug)
    record = workstore.get_work(work_slug)
    project_slug = record.work.project_slug if record is not None else None
    share_roots = resolve_share_roots(project_slug) if project_slug else []
    agents = workstore.list_agents_for_work(work_slug)
    agent_id_to_slug: dict[int, str | None] = {
        a.id: a.slug for a in agents if a.id is not None
    }
    context = _ViewContext(
        work_slug=work_slug,
        agent_id_to_slug=agent_id_to_slug,
        share_roots=share_roots,
        resolve_worktree=resolve_worktree,
    )
    return [_to_view(art, context) for art in artifacts]


@dataclass(frozen=True)
class _ViewContext:
    """Bundles the cross-cutting deps + lookups the per-type ``_to_view``
    handlers need. Lets ``singledispatch`` dispatch on the artifact
    alone — the context rides as the second positional arg."""

    work_slug: str
    agent_id_to_slug: dict[int, str | None]
    share_roots: list[Path]
    resolve_worktree: WorktreeResolver


@singledispatch
def _to_view(artifact: object, context: _ViewContext) -> ArtifactView:
    """Project a persisted Artifact onto its read-side ``ArtifactView``.

    Dispatch is by runtime subclass — adding a new artifact type means
    registering a new handler below; no branch to update here.
    """
    raise RuntimeError(f"unhandled artifact subclass: {type(artifact).__name__}")


@_to_view.register
def _(artifact: PrArtifact, context: _ViewContext) -> ArtifactView:
    assert artifact.slug is not None
    return ArtifactView(
        slug=artifact.slug,
        type="pr",
        title=artifact.title,
        status=artifact.status,
        created_at=artifact.created_at,
        agent_slug=_agent_slug(artifact.agent_id, context),
        url=artifact.url,
        repo=artifact.repo,
        doc_path=None,
        location_kind=None,
    )


@_to_view.register
def _(artifact: JiraArtifact, context: _ViewContext) -> ArtifactView:
    assert artifact.slug is not None
    return ArtifactView(
        slug=artifact.slug,
        type="jira",
        title=artifact.title,
        status=artifact.status,
        created_at=artifact.created_at,
        agent_slug=_agent_slug(artifact.agent_id, context),
        url=artifact.url,
        repo=None,
        doc_path=None,
        location_kind=None,
    )


@_to_view.register
def _(artifact: DocArtifact, context: _ViewContext) -> ArtifactView:
    assert artifact.slug is not None
    agent_slug = _agent_slug(artifact.agent_id, context)
    worktree = (
        context.resolve_worktree(context.work_slug, agent_slug)
        if agent_slug
        else None
    )
    location_kind = classify_location(
        Path(artifact.doc_path),
        worktree=worktree,
        share_roots=context.share_roots,
    )
    return ArtifactView(
        slug=artifact.slug,
        type="doc",
        title=artifact.title,
        status=_derive_doc_status(Path(artifact.doc_path), location_kind),
        created_at=artifact.created_at,
        agent_slug=agent_slug,
        url=None,
        repo=None,
        doc_path=artifact.doc_path,
        location_kind=location_kind,
    )


def _agent_slug(
    agent_id: int | None, context: _ViewContext
) -> str | None:
    if agent_id is None:
        return None
    return context.agent_id_to_slug.get(agent_id)


def _derive_doc_status(
    doc_path: Path, location_kind: LocationKind | None
) -> DocStatus:
    """Derived state collapse: shared → draft, worktree → pending or
    committed based on git, unknown → draft. The persisted column isn't
    consulted: doc artifacts use observed state, not author-declared."""
    if location_kind == "shared":
        return "draft"
    if location_kind == "worktree":
        gs = git_state(doc_path)
        return "committed" if gs == "committed" else "pending"
    return "draft"


__all__ = ["ArtifactView", "execute"]
