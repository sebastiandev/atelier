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

from src.domain.artifacts.models import PrArtifact
from src.domain.models import Agent, AgentStatus, Artifact, Context, Handoff, Work
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

    def count_children_by_work_id(self) -> dict[int, dict[str, int]]:
        """Aggregate agent/artifact counts per work, keyed by ``Work.id``.

        Returned shape: ``{work_id: {"agents": N, "artifacts": M}}``. Missing
        work ids should be treated as zero on both axes; the caller does the
        join with ``list_works()``.
        """
        ...

    def update_work(self, req: UpdateWorkRequest) -> WorkRecord: ...

    def move_work_to_project(
        self, work_slug: str, project_slug: str | None
    ) -> WorkRecord:
        """Re-parent a work to a different project (or to ``None`` for Loose).

        Updates the SQL row and rewrites ``work.json`` so reconcile won't
        revert the change on next startup. Validation that the target
        project exists is the caller's responsibility — the WorkStore
        port has no direct view of ProjectStore.
        """
        ...

    def soft_delete_work(self, work_slug: str) -> None: ...

    def add_agent_to_work(self, req: AddAgentRequest) -> Agent: ...

    def delete_agent(self, agent_slug: str) -> None:
        """Remove an agent end-to-end: workspace dir (transcript, agent.json,
        contexts) + DB row. Caller is responsible for stopping the supervisor
        and removing any per-agent worktree first — this method only handles
        what the workstore owns."""
        ...

    def render_agent_contexts(
        self,
        work_slug: str,
        agent_slug: str,
        contexts: list[Context],
        fetched_bodies: dict[int, str] | None = None,
        *,
        since_index: int = 0,
    ) -> str | None: ...

    def list_agents_for_work(self, work_slug: str) -> list[Agent]: ...

    def get_work_slug_for_agent(self, agent_slug: str) -> str | None: ...

    def get_agent_contexts(self, work_slug: str, agent_slug: str) -> list[Context]:
        """Read the agent's context list from ``agent.json``. Returns
        an empty list when the agent has no contexts (or no agent.json
        — treat both as 'no contexts'; the renderer no-ops on empty)."""
        ...

    def replace_agent_contexts(
        self, work_slug: str, agent_slug: str, contexts: list[Context]
    ) -> None:
        """Persist the merged contexts list back to ``agent.json``.
        Used by the mid-session add-context flow after the renderer has
        written the new per-source files."""
        ...

    def set_agent_session_id(self, agent_slug: str, session_id: str) -> None: ...

    def set_agent_status(self, agent_slug: str, status: AgentStatus) -> None: ...

    def append_transcript_event(
        self, work_slug: str, agent_slug: str, event: dict[str, Any]
    ) -> None: ...

    def append_transcript_event_with_seq(
        self, work_slug: str, agent_slug: str, payload: dict[str, Any]
    ) -> int:
        """Stamp the next monotonic ``seq`` and append. Returns the seq.

        For writes outside the supervisor's hot-path (e.g. transcript
        markers from sync route handlers, the CLI catch-up merge). The
        supervisor itself manages seq under its own lock; this method
        is for moments when no supervisor lock is in play.
        """
        ...

    def read_transcript_from_cursor(
        self, work_slug: str, agent_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]: ...

    def find_last_detach_cursor(
        self, work_slug: str, agent_slug: str
    ) -> dict[str, Any] | None:
        """Walk the NDJSON transcript and return the ``sdk_cursor`` payload
        from the most recent ``user_detached`` marker, or ``None`` if no
        detach is recorded. Used by re-attach catch-up to know where the
        SDK file's "new" entries start."""
        ...

    def is_session_ingested(
        self, work_slug: str, agent_slug: str, session_id: str
    ) -> bool:
        """True if the NDJSON ledger already contains content for the given
        provider session — either via a ``session_established`` event
        (supervisor streamed it live) or a ``sdk_session_merged`` marker
        (catch-up merged it from the SDK file). Used to dedup parent-chain
        merges so a re-attach doesn't re-import an already-ingested
        ancestor session."""
        ...

    def record_artifact(self, req: RecordArtifactRequest) -> Artifact: ...

    def list_artifacts_for_work(self, work_slug: str) -> list[Artifact]: ...

    def get_artifact_by_slug(self, slug: str) -> Artifact | None: ...

    def list_non_terminal_pr_artifacts(self) -> list[tuple[str, PrArtifact]]:
        """All ``PrArtifact`` rows whose status is non-terminal
        (currently ``open`` / ``draft``), paired with their parent
        work's slug. Used by the background PR status poller: terminal
        rows (``merged`` / ``closed``) are absorbing states and never
        need a refresh."""
        ...

    def update_artifact_status(self, slug: str, status: str) -> None:
        """Persist a new status on an existing artifact. Validated
        against the type's allowed vocabulary — invalid values raise
        ``InvalidStatus``. No-op if the row is gone."""
        ...

    def record_handoff(self, req: RecordHandoffRequest) -> Handoff: ...

    def list_handoffs_for_work(self, work_slug: str) -> list[Handoff]: ...


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
    def count_children_by_work_id(self) -> dict[int, dict[str, int]]: ...

    # Agent
    def add_agent(self, agent: Agent) -> Agent: ...
    def upsert_agent(self, agent: Agent) -> Agent: ...
    def delete_agent(self, agent_slug: str) -> None: ...
    def get_agent_by_slug(self, slug: str) -> Agent | None: ...
    def list_agents_for_work(self, work_slug: str) -> list[Agent]: ...
    def get_work_slug_for_agent(self, agent_slug: str) -> str | None: ...
    def set_agent_session_id(self, agent_slug: str, session_id: str) -> None: ...
    def set_agent_status(self, agent_slug: str, status: AgentStatus) -> None: ...

    # Artifact / Handoff (add only — reconcile of these lives in later stories)
    def add_artifact(self, artifact: Artifact) -> Artifact: ...
    def list_artifacts_for_work(self, work_slug: str) -> list[Artifact]: ...
    def get_artifact_by_slug(self, slug: str) -> Artifact | None: ...
    def list_non_terminal_pr_artifacts(self) -> list[tuple[str, PrArtifact]]: ...
    def update_artifact_status(self, slug: str, status: str) -> None: ...
    def add_handoff(self, handoff: Handoff) -> Handoff: ...
    def list_handoffs_for_work(self, work_slug: str) -> list[Handoff]: ...


class WorkspaceFiles(Protocol):
    """Atomic-replace filesystem metadata under the workspace root."""

    def ensure_work_dir(self, work_slug: str) -> None: ...
    def ensure_agent_dir(self, work_slug: str, agent_slug: str) -> None: ...
    def remove_agent_dir(self, work_slug: str, agent_slug: str) -> None:
        """Recursively remove the agent's workspace dir (transcript, agent.json,
        contexts/). Idempotent — missing dir is a no-op."""
        ...

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
