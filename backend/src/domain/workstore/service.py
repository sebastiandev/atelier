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

from src.domain.models import Agent, Artifact, Handoff, Work
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
                folder=req.folder,
                status="active",
                created_at=self._clock(),
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

    def soft_delete_work(self, work_slug: str) -> None:
        with self._lock:
            existing = self._require_work(work_slug)
            data = self._files.read_work_json(work_slug)
            contexts = deserialize_contexts(data) if data is not None else []
            existing.status = "deleted"
            self._repo.upsert_work(existing)
            self._files.write_work_json(work_slug, serialize_work_record(existing, contexts))

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
                status="idle",
                started_at=self._clock(),
            )
            agent = self._repo.add_agent(agent)
            slug = _require_slug(agent)
            self._files.ensure_agent_dir(req.work_slug, slug)
            self._files.write_agent_json(req.work_slug, slug, serialize_agent(agent))
        return agent

    def list_agents_for_work(self, work_slug: str) -> list[Agent]:
        with self._lock:
            self._require_work(work_slug)
            return self._repo.list_agents_for_work(work_slug)

    def append_transcript_event(
        self, work_slug: str, agent_slug: str, event: dict[str, Any]
    ) -> None:
        # Intentionally outside the service-level lock: the supervisor
        # (STORY-009) owns single-writer semantics per agent transcript,
        # and the underlying NDJSON layer is crash-safe on its own.
        self._transcript_log.append(work_slug, agent_slug, event)

    def read_transcript_from_cursor(
        self, work_slug: str, agent_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]:
        return self._transcript_log.read_from_cursor(work_slug, agent_slug, cursor)

    def record_artifact(self, req: RecordArtifactRequest) -> Artifact:
        with self._lock:
            parent = self._require_work(req.work_slug)
            agent_id: int | None = None
            if req.agent_slug is not None:
                agent = self._repo.get_agent_by_slug(req.agent_slug)
                if agent is None:
                    raise ValueError(f"agent not found: {req.agent_slug}")
                agent_id = agent.id
            artifact = Artifact(
                work_id=_require_id(parent),
                agent_id=agent_id,
                type=req.type,
                title=req.title,
                status=req.status,
                created_at=self._clock(),
                repo=req.repo,
                url=req.url,
                doc_path=req.doc_path,
            )
            return self._repo.add_artifact(artifact)

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
