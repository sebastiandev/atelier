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
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Literal types (enums)
# ---------------------------------------------------------------------------

WorkStatus = Literal["active", "completed"]
ContextType = Literal["sentry", "honeycomb", "jira", "url", "text", "file", "agentout"]
Persona = Literal["architect", "developer", "product", "ux", "writer", "custom"]
Provider = Literal["claude-code", "amp", "codex"]
AgentStatus = Literal["live", "thinking", "idle"]
ConnectionType = Literal["sentry", "honeycomb", "jira"]
ArtifactType = Literal["pr", "jira", "doc"]
HandoffTargetDialog = Literal["new-agent"]


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
    """

    id: int | None = None
    slug: str | None = None
    name: str
    description: str
    folder: Path
    status: WorkStatus
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
    status: AgentStatus
    started_at: datetime
    stopped_at: datetime | None = None


@dataclass(kw_only=True)
class Connection:
    """Source-system credential metadata.

    The actual token never lives on the entity — it sits in the OS keychain
    under a key derived from `slug` (e.g. `f"atelier:{conn.slug}"`). The
    keyring reference is therefore not stored on the entity or in SQLite.
    """

    id: int | None = None
    slug: str | None = None
    type: ConnectionType
    name: str
    created_at: datetime
    url: str | None = None
    org: str | None = None
    region: str | None = None
    env: str | None = None
    team: str | None = None
    email: str | None = None
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
    "Provider",
    "Work",
    "WorkStatus",
]
