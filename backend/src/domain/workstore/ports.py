"""Ports for the WorkStore boundary.

`WorkStore` is the public port — what application-layer commands depend on.
The other three ports decompose its implementation into testable pieces:

  - `WorkRepository` — SQL-side row operations on Work/Agent/Artifact/Handoff.
  - `WorkspaceFiles` — atomic filesystem metadata (work.json, brief.md,
    agent.json, handoff docs).
  - `TranscriptLog` — append-only NDJSON transcript log.

`WorkStoreService` (in `service.py`) implements `WorkStore` using all three;
`reconcile` (in `reconcile.py`) uses `WorkRepository` + `WorkspaceFiles`.

Domain stays framework-free — these Protocols expose only stdlib + domain
types.
"""

from collections.abc import Iterator
from typing import Any, Protocol

from src.domain.models import Agent, Artifact, Context, Handoff, Work
from src.domain.workstore.dtos import (
    AddAgentRequest,
    CreateWorkRequest,
    RecordArtifactRequest,
    RecordHandoffRequest,
    UpdateWorkRequest,
    WorkRecord,
)


class WorkStore(Protocol):
    """Public persistence boundary for Work and its children."""

    def create_work(self, req: CreateWorkRequest) -> WorkRecord: ...

    def get_work(self, work_slug: str) -> WorkRecord | None: ...

    def list_works(self) -> list[Work]: ...

    def update_work(self, req: UpdateWorkRequest) -> WorkRecord: ...

    def soft_delete_work(self, work_slug: str) -> None: ...

    def add_agent_to_work(self, req: AddAgentRequest) -> Agent: ...

    def render_agent_contexts(
        self,
        work_slug: str,
        agent_slug: str,
        contexts: list[Context],
        fetched_bodies: dict[int, str] | None = None,
    ) -> str | None: ...

    def list_agents_for_work(self, work_slug: str) -> list[Agent]: ...

    def get_work_slug_for_agent(self, agent_slug: str) -> str | None: ...

    def set_agent_session_id(self, agent_slug: str, session_id: str) -> None: ...

    def append_transcript_event(
        self, work_slug: str, agent_slug: str, event: dict[str, Any]
    ) -> None: ...

    def read_transcript_from_cursor(
        self, work_slug: str, agent_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]: ...

    def record_artifact(self, req: RecordArtifactRequest) -> Artifact: ...

    def record_handoff(self, req: RecordHandoffRequest) -> Handoff: ...


class WorkRepository(Protocol):
    """SQL-side row operations.

    `add_*` methods allocate the slug from the DB-assigned id (e.g.
    ``f"WRK-{id:03d}"``) and return the entity with both ``id`` and
    ``slug`` populated. ``upsert_*`` methods take a slug-bearing entity
    (the caller already knows the id) and insert-or-update.
    """

    # Work
    def add_work(self, work: Work) -> Work: ...
    def upsert_work(self, work: Work) -> Work: ...
    def delete_work(self, work_slug: str) -> None: ...
    def get_work_by_slug(self, slug: str) -> Work | None: ...
    def list_works(self) -> list[Work]: ...

    # Agent
    def add_agent(self, agent: Agent) -> Agent: ...
    def upsert_agent(self, agent: Agent) -> Agent: ...
    def delete_agent(self, agent_slug: str) -> None: ...
    def get_agent_by_slug(self, slug: str) -> Agent | None: ...
    def list_agents_for_work(self, work_slug: str) -> list[Agent]: ...
    def get_work_slug_for_agent(self, agent_slug: str) -> str | None: ...
    def set_agent_session_id(self, agent_slug: str, session_id: str) -> None: ...

    # Artifact / Handoff (add only — reconcile of these lives in later stories)
    def add_artifact(self, artifact: Artifact) -> Artifact: ...
    def add_handoff(self, handoff: Handoff) -> Handoff: ...


class WorkspaceFiles(Protocol):
    """Atomic-replace filesystem metadata under the workspace root."""

    def ensure_work_dir(self, work_slug: str) -> None: ...
    def ensure_agent_dir(self, work_slug: str, agent_slug: str) -> None: ...

    def write_work_json(self, work_slug: str, data: dict[str, Any]) -> None: ...
    def read_work_json(self, work_slug: str) -> dict[str, Any] | None: ...

    def write_brief(self, work_slug: str, content: str) -> None: ...

    def write_agent_json(self, work_slug: str, agent_slug: str, data: dict[str, Any]) -> None: ...
    def read_agent_json(self, work_slug: str, agent_slug: str) -> dict[str, Any] | None: ...

    def write_handoff_doc(self, work_slug: str, filename: str, content: str) -> str: ...

    def write_agent_context_file(
        self, work_slug: str, agent_slug: str, filename: str, content: str
    ) -> str: ...
    def write_agent_context_index(
        self, work_slug: str, agent_slug: str, content: str
    ) -> str: ...

    def list_work_slugs(self) -> list[str]: ...
    def list_agent_slugs(self, work_slug: str) -> list[str]: ...


class TranscriptLog(Protocol):
    """Append-only NDJSON event log per agent."""

    def append(self, work_slug: str, agent_slug: str, event: dict[str, Any]) -> None: ...

    def read_from_cursor(
        self, work_slug: str, agent_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]: ...

    def last_seq(self, work_slug: str, agent_slug: str) -> int:
        """Highest ``seq`` currently on disk; ``0`` if the log is empty.

        The supervisor calls this on ``start_agent`` to seed the per-agent
        seq counter so resume continues monotonically instead of
        restarting at 1 and overwriting history. Implementations should
        avoid reading the whole file (tail-read) once the log is large.
        """
        ...


__all__ = [
    "TranscriptLog",
    "WorkRepository",
    "WorkStore",
    "WorkspaceFiles",
]
