"""Typed artifact hierarchy.

``BaseArtifact`` holds the identity/lineage every artifact shares.
``PrArtifact`` / ``JiraArtifact`` / ``DocArtifact`` add the columns the
type actually uses plus a typed ``status`` literal.

The SA mapping (``infrastructure/database/mapping.py``) registers
``BaseArtifact`` as the polymorphic root over the single ``artifacts``
table, with ``polymorphic_on=type`` dispatching each row to the right
subclass at load time. Empty/None columns stay None on subclasses that
don't use them — the table has every column nullable.

Why dataclasses with default values for SA-mapped fields:
SA's imperative mapper sets attributes during unpickling/load via
``__init__`` (when ``init=True``). Keyword-only + defaulted fields let
the loader call ``Subclass(**row_dict)`` without caring about argument
order across base + subclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ArtifactType = Literal["pr", "doc", "jira"]

PrStatus = Literal["draft", "open", "merged", "closed"]
DocStatus = Literal["draft", "pending", "committed"]
JiraStatus = Literal[
    "todo", "in_progress", "in_review", "done", "closed", "blocked"
]


@dataclass(kw_only=True)
class BaseArtifact:
    """Shared identity + lineage for every artifact type.

    Loadable on its own only via the SA mapping's polymorphic dispatch;
    direct instantiation is fine for tests but real entities should be
    one of the concrete subclasses below.
    """

    id: int | None = None
    slug: str | None = None
    work_id: int
    agent_id: int | None
    type: ArtifactType
    title: str
    status: str
    created_at: datetime


@dataclass(kw_only=True)
class PrArtifact(BaseArtifact):
    """Pull-request artifact. ``url`` is the canonical identifier
    (``https://github.com/...``); ``repo`` is an optional grouping
    shorthand the FE uses to badge multi-repo work.

    ``pr_etag`` carries the GitHub response ETag from the last
    successful fetch so the poller can send ``If-None-Match`` and let
    304s skip the rate-limit budget. ``None`` on PRs we've never
    fetched against (e.g. just-recorded artifact, or a 404 fallback).
    """

    type: ArtifactType = "pr"
    status: PrStatus  # type: ignore[assignment]
    url: str
    repo: str | None = None
    pr_etag: str | None = None


@dataclass(kw_only=True)
class JiraArtifact(BaseArtifact):
    """Jira (or Jira-like) ticket artifact."""

    type: ArtifactType = "jira"
    status: JiraStatus  # type: ignore[assignment]
    url: str


@dataclass(kw_only=True)
class DocArtifact(BaseArtifact):
    """Document artifact (design notes, ADRs, plans, READMEs, stories…).

    ``status`` carries the derived view: ``draft`` for shared-folder
    docs, ``pending`` for worktree docs that don't match HEAD,
    ``committed`` for worktree docs that do. The recorder always
    persists ``draft`` for new doc rows; the listing layer re-derives
    on read.
    """

    type: ArtifactType = "doc"
    status: DocStatus  # type: ignore[assignment]
    doc_path: str


# Public union for type hints — narrows on the runtime subclass.
Artifact = PrArtifact | JiraArtifact | DocArtifact


def make_artifact(
    *,
    type: ArtifactType,
    work_id: int,
    agent_id: int | None,
    title: str,
    status: str,
    created_at: datetime,
    url: str | None = None,
    repo: str | None = None,
    doc_path: str | None = None,
    id: int | None = None,
    slug: str | None = None,
) -> Artifact:
    """Dispatcher that constructs the right concrete subclass.

    The recorder layer (``workstore.record_artifact``) calls this so
    its caller — the route or the artifact tracker — can stay
    type-agnostic and pass a flat ``RecordArtifactRequest``. Validation
    of per-type required fields happens here so a malformed payload
    surfaces a clear ``ValueError`` instead of a missing-argument
    ``TypeError`` from the dataclass.
    """
    if type == "pr":
        if url is None:
            raise ValueError("PR artifact requires url")
        return PrArtifact(
            id=id,
            slug=slug,
            work_id=work_id,
            agent_id=agent_id,
            title=title,
            status=status,  # type: ignore[arg-type]
            created_at=created_at,
            url=url,
            repo=repo,
        )
    if type == "jira":
        if url is None:
            raise ValueError("Jira artifact requires url")
        return JiraArtifact(
            id=id,
            slug=slug,
            work_id=work_id,
            agent_id=agent_id,
            title=title,
            status=status,  # type: ignore[arg-type]
            created_at=created_at,
            url=url,
        )
    if type == "doc":
        if doc_path is None:
            raise ValueError("Doc artifact requires doc_path")
        return DocArtifact(
            id=id,
            slug=slug,
            work_id=work_id,
            agent_id=agent_id,
            title=title,
            status=status,  # type: ignore[arg-type]
            created_at=created_at,
            doc_path=doc_path,
        )
    raise ValueError(f"unknown artifact type: {type!r}")


__all__ = [
    "Artifact",
    "ArtifactType",
    "BaseArtifact",
    "DocArtifact",
    "DocStatus",
    "JiraArtifact",
    "JiraStatus",
    "PrArtifact",
    "PrStatus",
    "make_artifact",
]
