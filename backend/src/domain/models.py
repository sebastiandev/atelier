"""Domain entities for Atelier.

Plain dataclasses, no framework imports. These are the types business commands
operate on; the SQLAlchemy mapping in `infrastructure/database/mapping.py` binds
them to tables imperatively without polluting this module.

Notes:
- Not frozen, not slotted. SA's imperative mapping populates instances via
  `__new__` + setattr at load time, which both attributes would block.
- Identity model: each persisted entity carries an integer PK (`id`) and a
  human-readable `slug` ("WRK-001", "agt-7", "con-3", ...). Internal SQL FKs
  use the int PK; URLs, folder names, and JSON cross-references use the slug.
  Both fields default to None on the dataclass; the repository populates them
  during create/persist. The DB columns are NOT NULL — slug must be set before
  flush.
- All entities are `kw_only=True` so optional `id`/`slug` can sit at the top
  while required business fields don't need defaults.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Literal types (enums)
# ---------------------------------------------------------------------------

WorkStatus = Literal["active", "completed", "deleted"]
ContextType = Literal["sentry", "honeycomb", "jira", "url", "text", "file", "agentout"]
Persona = Literal["architect", "developer", "product", "ux", "writer", "custom"]
Provider = Literal["claude-code", "amp"]
ConnectionType = Literal["sentry", "honeycomb", "jira"]
ArtifactType = Literal["pr", "jira", "doc"]
HandoffTargetDialog = Literal["new-agent"]


class AgentStatus(StrEnum):
    """Lifecycle states for an Agent.

    `StrEnum` so equality with bare strings still works (`status == "idle"`
    keeps reading correctly) and JSON serialisation falls through to the
    string value. Imperative SA mapping round-trips via the
    `AgentStatusType` decorator on `agents.status` so the dataclass field
    is the typed enum on read, not a plain string.
    """

    LIVE = "live"
    THINKING = "thinking"
    IDLE = "idle"
    STOPPED = "stopped"
    DETACHED = "detached"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """A piece of context attached to a Work or Agent.

    Lives in `work.json` on the filesystem, not in SQLite — hence not mapped.
    `conn_id` is a Connection slug (e.g. "con-3"), not an int PK, so the JSON
    file stays self-contained and human-readable.
    """

    type: ContextType
    value: str
    conn_id: str | None = None


@dataclass(kw_only=True)
class Work:
    """Work meta. Children (agents, artifacts, contexts) are fetched separately.

    The frontend's `WorkUnit` shape (per design handoff) embeds children; that
    aggregation happens in the `application/http/schemas.py` response model,
    not on this entity.

    Note: a Work is a *goal*, not a location. The directory each agent
    operates in lives on the Agent entity — agents in the same Work can
    target different repos (e.g. a frontend-repo agent + a backend-repo
    agent collaborating on one cross-cutting task).

    ``project_slug`` is the optional grouping link. Slug rather than int
    PK so work.json (FS-canonical) stays self-contained and human-readable
    — same precedent as ``Context.conn_id``. ON DELETE SET NULL at the SQL
    level: deleting a project demotes its works to "loose" rather than
    cascading.
    """

    id: int | None = None
    slug: str | None = None
    name: str
    description: str
    status: WorkStatus
    created_at: datetime
    project_slug: str | None = None


@dataclass(kw_only=True)
class Project:
    """Optional grouping above Work. Pure metadata — owns no folders.

    Works that share a project inherit its color (a single OKLCH hue) for
    visual grouping in the UI and pull default Jira/Sentry connections at
    use-time (read-through, not denormalized onto the Work). Works without
    a project ("loose work") remain first-class.

    ``glyph`` is a 1–2-character monogram derived from ``name`` on create
    by the FE; persisted so the user can override later.
    ``color`` is an OKLCH hue 0–360, exposed to CSS as ``--proj-h``.
    Default-connection fields hold connection slugs (e.g. ``"con-3"``);
    the FK is to ``connections.slug`` so deleting a connection sets the
    field to NULL rather than dangling.
    """

    id: int | None = None
    slug: str | None = None
    name: str
    description: str
    glyph: str
    color: int
    pinned: bool = False
    default_jira_conn: str | None = None
    default_sentry_conn: str | None = None
    created_at: datetime


@dataclass(kw_only=True)
class SharedFolder:
    """Persistent cross-agent folder scoped to a Project.

    Bridges "uncommitted personal planning" content (BMAD outputs,
    runbooks, scratch notes) across every agent in a project. Outlives
    individual agent worktrees and is invisible to git — the third
    bucket of agent context alongside source repo (committed) and
    per-agent worktree (transient scratch).

    Two paths matter:
      - The **canonical** path is derived from project + slug:
        ``<workspace>/projects/<PRJ>/shared/<share-slug>``. Agent
        worktrees symlink into this path at ``mount_path``.
      - The **real** path is where the data actually lives. ``None``
        when same as canonical (default-location share). When custom
        (adopted or new-with-custom-location), the canonical path is
        itself a symlink to ``real_path``, so the OS chases it
        transparently.

    ``mount_path`` is the relative path inside each agent's worktree
    where the share appears (e.g. ``_bmad-output/``). Immutable
    post-creation; ``name`` is the user-editable display label.
    """

    id: int | None = None
    slug: str | None = None
    project_id: int
    name: str
    mount_path: str
    real_path: Path | None = None
    created_at: datetime


@dataclass(kw_only=True)
class Agent:
    """Agent meta + lifecycle. UI session state lives on the frontend.

    Excluded by design (frontend-local, not domain):
      - `pinned`, `x`, `y` — view preferences kept in Zustand + localStorage,
        keyed by agent slug. They survive a page refresh, not a wipe.
      - `glyph` — derivable from `persona` via the frontend's persona catalog.
    """

    id: int | None = None
    slug: str | None = None
    work_id: int
    name: str
    persona: Persona
    role: str
    provider: Provider
    model: str
    folder: Path
    status: AgentStatus
    started_at: datetime
    stopped_at: datetime | None = None
    # Provider session/thread ID once the SDK has assigned one. Used to
    # resume the same conversation on reconnect: passed as ``resume`` to
    # the Claude SDK or ``continue_thread`` to the Amp SDK.
    session_id: str | None = None
    # Linked-list lineage of provider sessions. Some providers (Amp's
    # `--execute --stream-json`) fork on resume; ``parent_session_id``
    # points at the previous session whose visual transcript still
    # belongs to this agent. Walking the chain reconstructs the full
    # message history across forks at re-attach time.
    parent_session_id: str | None = None
    # Provider-specific options the user picked at create time. Shape
    # mirrors the ``options`` dict that ``Spec.build`` validates
    # (``permission_mode``, ``thinking_effort``, ``custom_allowed_tools``,
    # …). Persisted so resume rebuilds the same config and detach can
    # forward matching CLI flags. ``None`` on rows that predate this
    # field — callers must treat that as "use provider defaults".
    options: dict[str, Any] | None = None


@dataclass(kw_only=True)
class Connection:
    """Source-system credential metadata.

    The actual token never lives on the entity — it sits in the OS keychain
    under a key derived from `slug` (e.g. `f"atelier:{conn.slug}"`). The
    keyring reference is therefore not stored on the entity or in SQLite.

    ``config`` is a typed-per-type dataclass (``JiraConfig`` / ``SentryConfig``
    / ``HoneycombConfig``) carrying the fields specific to that source —
    e.g. Jira's ``url`` + ``email``. Stored as JSON on the SQL row;
    deserialised back to the right type via ``configs.dict_to_config``.
    Verifier + fetcher dispatch on ``type(config)`` via singledispatch.
    """

    id: int | None = None
    slug: str | None = None
    type: ConnectionType
    name: str
    created_at: datetime
    config: object  # ConnectionConfig — typed via configs.py to avoid a circular import.
    verified: bool = False
    last_used: datetime | None = None


@dataclass(kw_only=True)
class Artifact:
    id: int | None = None
    slug: str | None = None
    work_id: int
    agent_id: int | None
    type: ArtifactType
    title: str
    status: str
    created_at: datetime
    repo: str | None = None
    url: str | None = None
    doc_path: str | None = None


@dataclass(kw_only=True)
class Handoff:
    id: int | None = None
    slug: str | None = None
    work_id: int
    source_agent_id: int
    doc_path: Path
    created_at: datetime
    target_agent_id: int | None = None
    target_dialog: HandoffTargetDialog | None = None

    def __post_init__(self) -> None:
        if (self.target_agent_id is None) == (self.target_dialog is None):
            raise ValueError(
                "Handoff must have exactly one of target_agent_id or target_dialog"
            )


__all__ = [
    "Agent",
    "AgentStatus",
    "Artifact",
    "ArtifactType",
    "Connection",
    "ConnectionType",
    "Context",
    "ContextType",
    "Handoff",
    "HandoffTargetDialog",
    "Persona",
    "Project",
    "Provider",
    "Work",
    "WorkStatus",
]
