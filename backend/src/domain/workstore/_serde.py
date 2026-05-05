"""JSON shape of work.json and agent.json.

Shared between `WorkStoreService` (writes) and `reconcile` (reads). Centralising
the encoder/decoder here keeps the on-disk schema in one place — change it,
and both ends update together.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from src.domain.models import Agent, Context, Work


def serialize_work_record(work: Work, contexts: list[Context]) -> dict[str, Any]:
    return {
        "id": work.id,
        "slug": work.slug,
        "name": work.name,
        "description": work.description,
        "folder": str(work.folder),
        "status": work.status,
        "created_at": work.created_at.isoformat(),
        "contexts": [_serialize_context(c) for c in contexts],
    }


def deserialize_work_record(data: dict[str, Any]) -> tuple[Work, list[Context]]:
    work = Work(
        id=data.get("id"),
        slug=data.get("slug"),
        name=data["name"],
        description=data["description"],
        folder=Path(data["folder"]),
        status=data["status"],
        created_at=datetime.fromisoformat(data["created_at"]),
    )
    return work, deserialize_contexts(data)


def deserialize_contexts(data: dict[str, Any]) -> list[Context]:
    raw = data.get("contexts", [])
    return [
        Context(type=item["type"], value=item["value"], conn_id=item.get("conn_id")) for item in raw
    ]


def serialize_agent(agent: Agent, contexts: list[Context] | None = None) -> dict[str, Any]:
    """Serialise an agent for ``agent.json``. ``contexts`` is FS-only — it
    mirrors ``Work.contexts``: not on the entity, passed as a sibling."""
    return {
        "id": agent.id,
        "slug": agent.slug,
        "work_id": agent.work_id,
        "name": agent.name,
        "persona": agent.persona,
        "role": agent.role,
        "provider": agent.provider,
        "model": agent.model,
        "status": agent.status,
        "started_at": agent.started_at.isoformat(),
        "stopped_at": agent.stopped_at.isoformat() if agent.stopped_at else None,
        "session_id": agent.session_id,
        "contexts": [_serialize_context(c) for c in (contexts or [])],
    }


def deserialize_agent(data: dict[str, Any]) -> Agent:
    stopped_raw = data.get("stopped_at")
    return Agent(
        id=data.get("id"),
        slug=data.get("slug"),
        work_id=data["work_id"],
        name=data["name"],
        persona=data["persona"],
        role=data["role"],
        provider=data["provider"],
        model=data["model"],
        status=data["status"],
        started_at=datetime.fromisoformat(data["started_at"]),
        stopped_at=datetime.fromisoformat(stopped_raw) if stopped_raw else None,
        session_id=data.get("session_id"),
    )


def _serialize_context(c: Context) -> dict[str, Any]:
    out: dict[str, Any] = {"type": c.type, "value": c.value}
    if c.conn_id is not None:
        out["conn_id"] = c.conn_id
    return out
