"""WorkStoreService — pure-domain implementation of the WorkStore port.

Composes a `WorkRepository` (SQL), `WorkspaceFiles` (atomic FS metadata),
and `TranscriptLog` (NDJSON) under a process-local `RLock`. Slug
allocation and FS↔DB ordering live here, so the policy is testable with
stub ports — no SQLite, no real filesystem.

Ordering: methods that touch both FS and DB persist DB first (the repo
commits per call) and then write FS. A crash between the two leaves an
orphan DB row, which the next startup `reconcile` reconciles against the
canonical filesystem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from src.domain.agents.context_render import render_agent_contexts
from src.domain.artifacts import Artifact, make_artifact, validate_status
from src.domain.artifacts.models import PrArtifact
from src.domain.models import Agent, AgentStatus, Context, Handoff, Work
from src.domain.workstore._serde import (
    deserialize_contexts,
    serialize_agent,
    serialize_work_record,
)
from src.domain.workstore.dtos import (
    AddAgentRequest,
    CreateWorkRequest,
    RecordArtifactRequest,
    RecordHandoffRequest,
    UpdateWorkRequest,
    WorkRecord,
)
from src.domain.workstore.ports import TranscriptLog, WorkRepository, WorkspaceFiles

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WorkStoreService:
    def __init__(
        self,
        repo: WorkRepository,
        files: WorkspaceFiles,
        transcript_log: TranscriptLog,
        *,
        lock: RLock | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._repo = repo
        self._files = files
        self._transcript_log = transcript_log
        self._lock = lock if lock is not None else RLock()
        self._clock = clock

    def create_work(self, req: CreateWorkRequest) -> WorkRecord:
        with self._lock:
            work = Work(
                name=req.name,
                description=req.description,
                status="active",
                created_at=self._clock(),
                project_slug=req.project_slug,
            )
            work = self._repo.add_work(work)
            slug = _require_slug(work)
            self._files.ensure_work_dir(slug)
            self._files.write_work_json(slug, serialize_work_record(work, list(req.contexts)))
            self._files.write_brief(slug, req.description)
        return WorkRecord(work=work, contexts=list(req.contexts))

    def get_work(self, work_slug: str) -> WorkRecord | None:
        with self._lock:
            work = self._repo.get_work_by_slug(work_slug)
            if work is None or work.status == "deleted":
                return None
            data = self._files.read_work_json(work_slug)
            contexts = deserialize_contexts(data) if data is not None else []
        return WorkRecord(work=work, contexts=contexts)

    def list_works(self) -> list[Work]:
        with self._lock:
            return [w for w in self._repo.list_works() if w.status != "deleted"]

    def count_children_by_work_id(self) -> dict[int, dict[str, int]]:
        with self._lock:
            return self._repo.count_children_by_work_id()

    def update_work(self, req: UpdateWorkRequest) -> WorkRecord:
        with self._lock:
            existing = self._require_work(req.work_slug)
            data = self._files.read_work_json(req.work_slug)
            existing_contexts = deserialize_contexts(data) if data is not None else []

            if req.name is not None:
                existing.name = req.name
            if req.description is not None:
                existing.description = req.description
            if req.status is not None:
                existing.status = req.status
            new_contexts = list(req.contexts) if req.contexts is not None else existing_contexts

            self._repo.upsert_work(existing)
            self._files.write_work_json(
                req.work_slug, serialize_work_record(existing, new_contexts)
            )
            if req.description is not None:
                self._files.write_brief(req.work_slug, req.description)
        return WorkRecord(work=existing, contexts=new_contexts)

    def move_work_to_project(
        self, work_slug: str, project_slug: str | None
    ) -> WorkRecord:
        with self._lock:
            existing = self._require_work(work_slug)
            data = self._files.read_work_json(work_slug)
            contexts = deserialize_contexts(data) if data is not None else []

            existing.project_slug = project_slug
            self._repo.upsert_work(existing)
            # Re-write work.json so reconcile sees the new project on startup.
            self._files.write_work_json(
                work_slug, serialize_work_record(existing, contexts)
            )
        return WorkRecord(work=existing, contexts=contexts)

    def soft_delete_work(self, work_slug: str) -> None:
        with self._lock:
            existing = self._require_work(work_slug)
            data = self._files.read_work_json(work_slug)
            contexts = deserialize_contexts(data) if data is not None else []
            existing.status = "deleted"
            self._repo.upsert_work(existing)
            self._files.write_work_json(work_slug, serialize_work_record(existing, contexts))

    def delete_agent(self, agent_slug: str) -> None:
        with self._lock:
            work_slug = self._repo.get_work_slug_for_agent(agent_slug)
            if work_slug is None:
                return
            # FS first: a crash between the two leaves an orphan dir on
            # disk (harmless, swept by the wipe script / manual cleanup)
            # rather than a DB row pointing at deleted files (which would
            # render as a broken rail entry until the next reconcile).
            self._files.remove_agent_dir(work_slug, agent_slug)
            self._repo.delete_agent(agent_slug)

    def add_agent_to_work(self, req: AddAgentRequest) -> Agent:
        with self._lock:
            parent = self._require_work(req.work_slug)
            agent = Agent(
                work_id=_require_id(parent),
                name=req.name,
                persona=req.persona,
                role=req.role,
                provider=req.provider,
                model=req.model,
                folder=req.folder,
                status=AgentStatus.IDLE,
                started_at=self._clock(),
                # Empty dict normalises to ``None`` so the column stores
                # NULL for "no options" — keeps the on-disk shape uniform
                # with rows created before this column existed.
                options=dict(req.options) if req.options else None,
            )
            agent = self._repo.add_agent(agent)
            slug = _require_slug(agent)
            self._files.ensure_agent_dir(req.work_slug, slug)
            self._files.write_agent_json(
                req.work_slug, slug, serialize_agent(agent, list(req.contexts))
            )
        return agent

    def render_agent_contexts(
        self,
        work_slug: str,
        agent_slug: str,
        contexts: list[Context],
        fetched_bodies: dict[int, str] | None = None,
        *,
        since_index: int = 0,
    ) -> str | None:
        """Write per-source files for ``contexts[since_index:]`` and
        rebuild the index from the full list. Returns the absolute path
        to ``context.md``, or ``None`` if ``contexts`` is empty.

        ``since_index > 0`` is the mid-session "add context" path — only
        the new entries' bodies are needed in ``fetched_bodies``, and
        pre-existing files on disk are kept as-is."""
        with self._lock:
            return render_agent_contexts(
                self._files,
                work_slug,
                agent_slug,
                contexts,
                fetched_bodies,
                since_index=since_index,
            )

    def list_agents_for_work(self, work_slug: str) -> list[Agent]:
        with self._lock:
            self._require_work(work_slug)
            return self._repo.list_agents_for_work(work_slug)

    def backfill_missing_session_ids_from_transcripts(self) -> int:
        """Repair legacy rows whose runtime session id was lost from SQL.

        Older reconciles treated ``agent.json`` as authoritative for
        runtime fields and could overwrite SQL ``session_id`` with NULL.
        The transcript still carries every ``session_established`` event,
        so replay those ids through the repository's normal setter to
        restore the current id and one-hop parent lineage.
        """
        repaired = 0
        with self._lock:
            for work in self._repo.list_works():
                if work.slug is None:
                    continue
                for agent in self._repo.list_agents_for_work(work.slug):
                    if agent.slug is None or agent.session_id is not None:
                        continue
                    session_ids = self._session_ids_from_transcript(
                        work.slug, agent.slug
                    )
                    if not session_ids:
                        continue
                    for session_id in session_ids:
                        self._repo.set_agent_session_id(agent.slug, session_id)
                    repaired += 1
        return repaired

    def _session_ids_from_transcript(self, work_slug: str, agent_slug: str) -> list[str]:
        session_ids: list[str] = []
        for event in self._transcript_log.read_from_cursor(work_slug, agent_slug, 0):
            if event.get("type") != "session_established":
                continue
            session_id = event.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                continue
            if session_ids and session_ids[-1] == session_id:
                continue
            session_ids.append(session_id)
        return session_ids

    def get_work_slug_for_agent(self, agent_slug: str) -> str | None:
        # Used by the WS handler to resolve the transcript path when
        # the supervisor has no live state for an agent (e.g. after a
        # backend restart). Single SQL join in the repo layer.
        with self._lock:
            return self._repo.get_work_slug_for_agent(agent_slug)

    def get_agent_contexts(self, work_slug: str, agent_slug: str) -> list[Context]:
        with self._lock:
            data = self._files.read_agent_json(work_slug, agent_slug)
            return deserialize_contexts(data) if data is not None else []

    def replace_agent_contexts(
        self, work_slug: str, agent_slug: str, contexts: list[Context]
    ) -> None:
        with self._lock:
            agent = self._repo.get_agent_by_slug(agent_slug)
            if agent is None:
                raise ValueError(f"agent not found: {agent_slug}")
            self._files.write_agent_json(
                work_slug, agent_slug, serialize_agent(agent, contexts)
            )

    def write_agent_compaction_doc(
        self, work_slug: str, agent_slug: str, filename: str, content: str
    ) -> str:
        with self._lock:
            if self._repo.get_agent_by_slug(agent_slug) is None:
                raise ValueError(f"agent not found: {agent_slug}")
            if self._repo.get_work_slug_for_agent(agent_slug) != work_slug:
                raise ValueError(f"agent {agent_slug} is not in work {work_slug}")
            return self._files.write_agent_compaction_doc(
                work_slug, agent_slug, filename, content
            )

    def read_agent_compaction_doc(
        self, work_slug: str, agent_slug: str, filename: str
    ) -> tuple[str, str] | None:
        with self._lock:
            if self._repo.get_agent_by_slug(agent_slug) is None:
                raise ValueError(f"agent not found: {agent_slug}")
            if self._repo.get_work_slug_for_agent(agent_slug) != work_slug:
                raise ValueError(f"agent {agent_slug} is not in work {work_slug}")
            return self._files.read_agent_compaction_doc(
                work_slug, agent_slug, filename
            )

    def set_agent_session_id(
        self, agent_slug: str, session_id: str, *, mirror_agent_json: bool = False
    ) -> None:
        # The supervisor hot path calls this from SessionEstablished events;
        # keep that DB-only. Explicit same-agent session replacement commands
        # opt into mirroring so FS-side inspection sees the new lineage too.
        with self._lock:
            work_slug = self._repo.get_work_slug_for_agent(agent_slug)
            self._repo.set_agent_session_id(agent_slug, session_id)
            if not mirror_agent_json or work_slug is None:
                return
            agent = self._repo.get_agent_by_slug(agent_slug)
            if agent is None:
                return
            data = self._files.read_agent_json(work_slug, agent_slug)
            contexts = deserialize_contexts(data) if data is not None else []
            self._files.write_agent_json(
                work_slug, agent_slug, serialize_agent(agent, contexts)
            )

    def set_agent_status(self, agent_slug: str, status: AgentStatus) -> None:
        # Used by the detach flow (→ "detached") and the WS re-attach
        # path that pulls an agent back from CLI (→ "idle"). Same single-
        # UPDATE shape as set_agent_session_id; agent.json is allowed to
        # lag and reconciles on next full upsert.
        with self._lock:
            self._repo.set_agent_status(agent_slug, status)

    def rename_agent(self, agent_slug: str, name: str) -> Agent:
        # Name is FS-canonical per reconcile's authority split (see
        # ``reconcile.py``: definition fields live in agent.json, runtime
        # fields in SQL). We update both atomically per-side: SQL row
        # for the live read paths, then rewrite agent.json so a backend
        # restart's reconcile pass doesn't revert the change.
        with self._lock:
            agent = self._repo.get_agent_by_slug(agent_slug)
            if agent is None:
                raise ValueError(f"agent not found: {agent_slug}")
            work_slug = self._repo.get_work_slug_for_agent(agent_slug)
            if work_slug is None:
                raise ValueError(f"work not found for agent: {agent_slug}")
            self._repo.set_agent_name(agent_slug, name)
            agent.name = name
            existing_contexts = self.get_agent_contexts(work_slug, agent_slug)
            self._files.write_agent_json(
                work_slug, agent_slug, serialize_agent(agent, existing_contexts)
            )
            return agent

    def append_transcript_event(
        self, work_slug: str, agent_slug: str, event: dict[str, Any]
    ) -> None:
        # Intentionally outside the service-level lock: the supervisor
        # (STORY-009) owns single-writer semantics per agent transcript,
        # and the underlying NDJSON layer is crash-safe on its own.
        self._transcript_log.append(work_slug, agent_slug, event)

    def append_transcript_event_with_seq(
        self, work_slug: str, agent_slug: str, payload: dict[str, Any]
    ) -> int:
        # For writes that happen WITHOUT the supervisor running for this
        # agent — detach markers, CLI catch-up merge. The supervisor is
        # by construction not stamping seqs concurrently for the same
        # agent in these paths (we stop it first), so a tail-read +
        # increment + append is safe.
        with self._lock:
            seq = self._transcript_log.last_seq(work_slug, agent_slug) + 1
            stamped = {"seq": seq, **payload}
            self._transcript_log.append(work_slug, agent_slug, stamped)
            return seq

    def read_transcript_from_cursor(
        self, work_slug: str, agent_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]:
        return self._transcript_log.read_from_cursor(work_slug, agent_slug, cursor)

    def find_last_detach_cursor(
        self, work_slug: str, agent_slug: str
    ) -> dict[str, Any] | None:
        # Walks the full NDJSON to find the latest CLI sync cursor. The
        # transcript is bounded in practice (one user, single-session) so
        # a full read is cheap; if it ever gets large we can remember the
        # cursor on the agent row instead.
        cursor: dict[str, Any] | None = None
        for event in self._transcript_log.read_from_cursor(work_slug, agent_slug, 0):
            if event.get("type") in ("user_detached", "user_reattached"):
                payload = event.get("sdk_cursor")
                if isinstance(payload, dict):
                    cursor = payload
        return cursor

    def is_session_ingested(
        self, work_slug: str, agent_slug: str, session_id: str
    ) -> bool:
        for event in self._transcript_log.read_from_cursor(work_slug, agent_slug, 0):
            if event.get("session_id") != session_id:
                continue
            if event.get("type") in ("session_established", "sdk_session_merged"):
                return True
        return False

    def record_artifact(self, req: RecordArtifactRequest) -> Artifact:
        with self._lock:
            validate_status(req.type, req.status)
            parent = self._require_work(req.work_slug)
            agent_id: int | None = None
            if req.agent_slug is not None:
                agent = self._repo.get_agent_by_slug(req.agent_slug)
                if agent is None:
                    raise ValueError(f"agent not found: {req.agent_slug}")
                agent_id = agent.id
            artifact = make_artifact(
                type=req.type,
                work_id=_require_id(parent),
                agent_id=agent_id,
                title=req.title,
                status=req.status,
                created_at=self._clock(),
                repo=req.repo,
                url=req.url,
                doc_path=req.doc_path,
            )
            return self._repo.add_artifact(artifact)

    def list_artifacts_for_work(self, work_slug: str) -> list[Artifact]:
        with self._lock:
            self._require_work(work_slug)
            return self._repo.list_artifacts_for_work(work_slug)

    def get_artifact_by_slug(self, slug: str) -> Artifact | None:
        return self._repo.get_artifact_by_slug(slug)

    def list_non_terminal_pr_artifacts(self) -> list[tuple[str, PrArtifact]]:
        with self._lock:
            return self._repo.list_non_terminal_pr_artifacts()

    def update_artifact_status(
        self, slug: str, status: str, *, pr_etag: str | None = None
    ) -> None:
        with self._lock:
            existing = self._repo.get_artifact_by_slug(slug)
            if existing is None:
                # Caller may have raced a delete — silently drop. The
                # poller doesn't care; it'll skip this slug next cycle.
                return
            validate_status(existing.type, status)
            self._repo.update_artifact_status(slug, status, pr_etag=pr_etag)

    def update_pr_artifact_etag(self, slug: str, pr_etag: str) -> None:
        with self._lock:
            self._repo.update_pr_artifact_etag(slug, pr_etag)

    def record_handoff(self, req: RecordHandoffRequest) -> Handoff:
        with self._lock:
            parent = self._require_work(req.work_slug)
            source = self._repo.get_agent_by_slug(req.source_agent_slug)
            if source is None:
                raise ValueError(f"source agent not found: {req.source_agent_slug}")
            target_id: int | None = None
            if req.target_agent_slug is not None:
                target = self._repo.get_agent_by_slug(req.target_agent_slug)
                if target is None:
                    raise ValueError(f"target agent not found: {req.target_agent_slug}")
                target_id = target.id
            doc_path = self._files.write_handoff_doc(req.work_slug, req.doc_filename, req.doc_text)
            handoff = Handoff(
                work_id=_require_id(parent),
                source_agent_id=_require_id(source),
                doc_path=Path(doc_path),
                created_at=self._clock(),
                target_agent_id=target_id,
                target_dialog=req.target_dialog,
            )
            return self._repo.add_handoff(handoff)

    def list_handoffs_for_work(self, work_slug: str) -> list[Handoff]:
        with self._lock:
            self._require_work(work_slug)
            return self._repo.list_handoffs_for_work(work_slug)

    def _require_work(self, work_slug: str) -> Work:
        work = self._repo.get_work_by_slug(work_slug)
        if work is None or work.status == "deleted":
            raise ValueError(f"work not found: {work_slug}")
        return work


def _require_slug(entity: Work | Agent) -> str:
    if entity.slug is None:
        raise RuntimeError(
            f"repository returned {type(entity).__name__} without slug — "
            "the adapter must populate it during add_*"
        )
    return entity.slug


def _require_id(entity: Work | Agent) -> int:
    if entity.id is None:
        raise RuntimeError(f"{type(entity).__name__} has no id; was it persisted?")
    return entity.id


__all__ = ["WorkStoreService"]
