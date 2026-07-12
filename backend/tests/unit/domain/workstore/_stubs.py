"""In-memory stubs implementing the WorkStore-side ports.

Used by the service and reconcile unit tests. They imitate the real
adapters' externally-visible behaviour (slug allocation, missing-file =
None, etc.) without touching SQLite or the filesystem. The reconcile
stubs additionally let tests pre-seed state.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from src.domain.models import Agent, Artifact, Handoff, Work


class StubRepository:
    def __init__(self) -> None:
        self.works: dict[str, Work] = {}
        self.agents: dict[str, Agent] = {}
        self.artifacts: dict[str, Artifact] = {}
        self.handoffs: dict[str, Handoff] = {}
        self._next_work_id = 1
        self._next_agent_id = 1
        self._next_artifact_id = 1
        self._next_handoff_id = 1

    # -- Work --

    def add_work(self, work: Work) -> Work:
        work.id = self._next_work_id
        self._next_work_id += 1
        work.slug = f"WRK-{work.id:03d}"
        self.works[work.slug] = work
        return work

    def upsert_work(self, work: Work) -> Work:
        if work.slug is None:
            raise ValueError("upsert_work requires slug")
        self.works[work.slug] = work
        return work

    def delete_work(self, work_slug: str) -> None:
        work = self.works.pop(work_slug, None)
        work_id = work.id if work is not None else None
        # Mimic FK ON DELETE CASCADE on agents.
        for agent_slug in [
            s for s, a in self.agents.items() if work_id is not None and a.work_id == work_id
        ]:
            self.agents.pop(agent_slug, None)

    def get_work_by_slug(self, slug: str) -> Work | None:
        return self.works.get(slug)

    def list_works(self) -> list[Work]:
        return list(self.works.values())

    def count_children_by_work_id(self) -> dict[int, dict[str, int]]:
        out: dict[int, dict[str, int]] = {}
        for agent in self.agents.values():
            out.setdefault(agent.work_id, {"agents": 0, "artifacts": 0})["agents"] += 1
        for artifact in self.artifacts.values():
            out.setdefault(artifact.work_id, {"agents": 0, "artifacts": 0})["artifacts"] += 1
        return out

    # -- Agent --

    def add_agent(self, agent: Agent) -> Agent:
        agent.id = self._next_agent_id
        self._next_agent_id += 1
        agent.slug = f"agt-{agent.id}"
        self.agents[agent.slug] = agent
        return agent

    def upsert_agent(self, agent: Agent) -> Agent:
        if agent.slug is None:
            raise ValueError("upsert_agent requires slug")
        self.agents[agent.slug] = agent
        return agent

    def delete_agent(self, agent_slug: str) -> None:
        self.agents.pop(agent_slug, None)

    def get_agent_by_slug(self, slug: str) -> Agent | None:
        return self.agents.get(slug)

    def list_agents_for_work(self, work_slug: str) -> list[Agent]:
        work = self.works.get(work_slug)
        if work is None or work.id is None:
            return []
        return [a for a in self.agents.values() if a.work_id == work.id]

    def get_work_slug_for_agent(self, agent_slug: str) -> str | None:
        agent = self.agents.get(agent_slug)
        if agent is None:
            return None
        return _resolve_work_slug(self, agent.work_id)

    def set_agent_session_id(self, agent_slug: str, session_id: str) -> None:
        agent = self.agents.get(agent_slug)
        if agent is None:
            return
        if agent.session_id is not None and agent.session_id != session_id:
            agent.parent_session_id = agent.session_id
        agent.session_id = session_id

    def set_agent_status(self, agent_slug: str, status: Any) -> None:
        agent = self.agents.get(agent_slug)
        if agent is not None:
            agent.status = status

    def set_agent_model(self, agent_slug: str, model: str) -> None:
        agent = self.agents.get(agent_slug)
        if agent is not None:
            agent.model = model

    # -- Artifact / Handoff --

    def add_artifact(self, artifact: Artifact) -> Artifact:
        artifact.id = self._next_artifact_id
        self._next_artifact_id += 1
        artifact.slug = f"art-{artifact.id}"
        self.artifacts[artifact.slug] = artifact
        return artifact

    def list_artifacts_for_work(self, work_slug: str) -> list[Artifact]:
        work = self.works.get(work_slug)
        if work is None or work.id is None:
            return []
        return [a for a in self.artifacts.values() if a.work_id == work.id]

    def get_artifact_by_slug(self, slug: str) -> Artifact | None:
        return self.artifacts.get(slug)

    def list_non_terminal_pr_artifacts(self) -> list[tuple[str, Artifact]]:
        work_by_id = {w.id: w for w in self.works.values()}
        out: list[tuple[str, Artifact]] = []
        for artifact in self.artifacts.values():
            if artifact.type != "pr":
                continue
            if artifact.status not in ("open", "draft"):
                continue
            work = work_by_id.get(artifact.work_id)
            if work is None or work.slug is None:
                continue
            out.append((work.slug, artifact))
        return out

    def update_artifact_status(
        self, slug: str, status: str, *, pr_etag: str | None = None
    ) -> None:
        artifact = self.artifacts.get(slug)
        if artifact is None:
            return
        artifact.status = status
        if pr_etag is not None and hasattr(artifact, "pr_etag"):
            artifact.pr_etag = pr_etag

    def update_pr_artifact_etag(self, slug: str, pr_etag: str) -> None:
        artifact = self.artifacts.get(slug)
        if artifact is None or artifact.type != "pr":
            return
        artifact.pr_etag = pr_etag

    def add_handoff(self, handoff: Handoff) -> Handoff:
        handoff.id = self._next_handoff_id
        self._next_handoff_id += 1
        handoff.slug = f"hnd-{handoff.id}"
        self.handoffs[handoff.slug] = handoff
        return handoff

    def list_handoffs_for_work(self, work_slug: str) -> list[Handoff]:
        work = self.works.get(work_slug)
        if work is None or work.id is None:
            return []
        return [h for h in self.handoffs.values() if h.work_id == work.id]


class StubFiles:
    def __init__(self) -> None:
        self.work_dirs: set[str] = set()
        self.agent_dirs: set[tuple[str, str]] = set()
        self.work_jsons: dict[str, dict[str, Any]] = {}
        self.briefs: dict[str, str] = {}
        self.agent_jsons: dict[tuple[str, str], dict[str, Any]] = {}
        self.handoff_docs: dict[tuple[str, str], str] = {}
        self.compaction_docs: dict[tuple[str, str, str], str] = {}
        self.context_files: dict[tuple[str, str, str], str] = {}
        self.context_indexes: dict[tuple[str, str], str] = {}
        self.work_chat_contexts: dict[tuple[str, str, str], str] = {}

    def ensure_work_dir(self, work_slug: str) -> None:
        self.work_dirs.add(work_slug)

    def ensure_agent_dir(self, work_slug: str, agent_slug: str) -> None:
        self.agent_dirs.add((work_slug, agent_slug))

    def remove_agent_dir(self, work_slug: str, agent_slug: str) -> None:
        self.agent_dirs.discard((work_slug, agent_slug))
        self.agent_jsons.pop((work_slug, agent_slug), None)
        self.context_indexes.pop((work_slug, agent_slug), None)
        for key in list(self.context_files):
            if key[0] == work_slug and key[1] == agent_slug:
                self.context_files.pop(key, None)

    def remove_work_dir(self, work_slug: str) -> None:
        self.work_dirs.discard(work_slug)
        self.work_jsons.pop(work_slug, None)
        self.briefs.pop(work_slug, None)
        for key in list(self.agent_dirs):
            if key[0] == work_slug:
                self.agent_dirs.discard(key)
        for store in (
            self.agent_jsons,
            self.handoff_docs,
            self.compaction_docs,
            self.context_files,
            self.context_indexes,
            self.work_chat_contexts,
        ):
            for key in list(store):
                if key[0] == work_slug:
                    store.pop(key, None)

    def write_work_json(self, work_slug: str, data: dict[str, Any]) -> None:
        self.work_jsons[work_slug] = data

    def read_work_json(self, work_slug: str) -> dict[str, Any] | None:
        return self.work_jsons.get(work_slug)

    def write_brief(self, work_slug: str, content: str) -> None:
        self.briefs[work_slug] = content

    def write_work_chat_context_file(self, work_slug: str, folder: Any) -> str:
        self.work_chat_contexts[(work_slug, folder.name, folder.context_filename)] = (
            folder.context_markdown
        )
        return (
            f"/stub/works/{work_slug}/chat-contexts/"
            f"{folder.name}/{folder.context_filename}"
        )

    def read_work_chat_context_file(
        self, work_slug: str, folder_name: str, filename: str
    ) -> tuple[str, str] | None:
        content = self.work_chat_contexts.get((work_slug, folder_name, filename))
        if content is None:
            return None
        return (
            f"/stub/works/{work_slug}/chat-contexts/{folder_name}/{filename}",
            content,
        )

    def work_chat_context_folder_path(self, work_slug: str, folder: Any) -> Path:
        return Path(f"/stub/works/{work_slug}/chat-contexts/{folder.name}")

    def write_agent_json(self, work_slug: str, agent_slug: str, data: dict[str, Any]) -> None:
        self.agent_jsons[(work_slug, agent_slug)] = data

    def read_agent_json(self, work_slug: str, agent_slug: str) -> dict[str, Any] | None:
        return self.agent_jsons.get((work_slug, agent_slug))

    def write_handoff_doc(self, work_slug: str, filename: str, content: str) -> str:
        self.handoff_docs[(work_slug, filename)] = content
        return f"/stub/works/{work_slug}/handoffs/{filename}"

    def write_agent_compaction_doc(
        self, work_slug: str, agent_slug: str, filename: str, content: str
    ) -> str:
        self.compaction_docs[(work_slug, agent_slug, filename)] = content
        return f"/stub/works/{work_slug}/agents/{agent_slug}/compactions/{filename}"

    def read_agent_compaction_doc(
        self, work_slug: str, agent_slug: str, filename: str
    ) -> tuple[str, str] | None:
        content = self.compaction_docs.get((work_slug, agent_slug, filename))
        if content is None:
            return None
        return (
            f"/stub/works/{work_slug}/agents/{agent_slug}/compactions/{filename}",
            content,
        )

    def write_agent_context_file(
        self, work_slug: str, agent_slug: str, filename: str, content: str
    ) -> str:
        self.context_files[(work_slug, agent_slug, filename)] = content
        return f"/stub/works/{work_slug}/agents/{agent_slug}/context/{filename}"

    def write_agent_context_index(
        self, work_slug: str, agent_slug: str, content: str
    ) -> str:
        self.context_indexes[(work_slug, agent_slug)] = content
        return f"/stub/works/{work_slug}/agents/{agent_slug}/context.md"

    def list_work_slugs(self) -> list[str]:
        return sorted(self.work_jsons.keys())

    def list_agent_slugs(self, work_slug: str) -> list[str]:
        return sorted(agent_slug for (ws, agent_slug) in self.agent_jsons.keys() if ws == work_slug)


class StubTranscriptLog:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def append(self, work_slug: str, agent_slug: str, event: dict[str, Any]) -> None:
        self.events.setdefault((work_slug, agent_slug), []).append(event)

    def read_from_cursor(
        self, work_slug: str, agent_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]:
        for ev in self.events.get((work_slug, agent_slug), []):
            seq = ev.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq > cursor:
                yield ev

    def read_before(
        self, work_slug: str, agent_slug: str, before_seq: int, limit: int
    ) -> Iterator[dict[str, Any]]:
        events = [
            ev
            for ev in self.events.get((work_slug, agent_slug), [])
            if isinstance(ev.get("seq"), int)
            and not isinstance(ev.get("seq"), bool)
            and ev["seq"] < before_seq
        ]
        yield from events[-limit:] if limit > 0 else []

    def read_tail(
        self,
        work_slug: str,
        agent_slug: str,
        cursor: int,
        limit: int,
        before_seq: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        events = []
        for ev in self.events.get((work_slug, agent_slug), []):
            seq = ev.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq <= cursor:
                continue
            if before_seq is not None and seq >= before_seq:
                continue
            events.append(ev)
        yield from events[-limit:] if limit > 0 else []

    def read_recent_by_type(
        self,
        work_slug: str,
        agent_slug: str,
        event_types: set[str],
        cursor: int,
        limit: int,
        before_seq: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        events = []
        for ev in self.events.get((work_slug, agent_slug), []):
            seq = ev.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq <= cursor:
                continue
            if before_seq is not None and seq >= before_seq:
                continue
            if ev.get("type") not in event_types:
                continue
            events.append(ev)
        yield from events[-limit:] if limit > 0 else []

    def last_seq(self, work_slug: str, agent_slug: str) -> int:
        seqs = [
            ev.get("seq")
            for ev in self.events.get((work_slug, agent_slug), [])
            if isinstance(ev.get("seq"), int) and not isinstance(ev.get("seq"), bool)
        ]
        return max(seqs, default=0)  # type: ignore[type-var]


def _resolve_work_slug(repo: StubRepository, work_id: int) -> str | None:
    for slug, w in repo.works.items():
        if w.id == work_id:
            return slug
    return None
