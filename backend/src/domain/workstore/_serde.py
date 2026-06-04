"""JSON shape of work.json and agent.json.

Shared between `WorkStoreService` (writes) and `reconcile` (reads). Centralising
the encoder/decoder here keeps the on-disk schema in one place — change it,
and both ends update together.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from src.domain.models import Agent, Context, Work
from src.domain.workstore.dtos import (
    WorkChatContextFolder,
    WorkChatProvenance,
)


def serialize_work_record(
    work: Work,
    contexts: list[Context],
    from_chat: WorkChatProvenance | None = None,
    chat_context_folders: list[WorkChatContextFolder] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": work.id,
        "slug": work.slug,
        "name": work.name,
        "description": work.description,
        "status": work.status,
        "created_at": work.created_at.isoformat(),
        "project_slug": work.project_slug,
        "contexts": [_serialize_context(c) for c in contexts],
    }
    provenance = from_chat
    if provenance is None and work.from_chat_slug and work.from_chat_title:
        provenance = WorkChatProvenance(
            chat_slug=work.from_chat_slug,
            chat_title=work.from_chat_title,
        )
    if provenance is not None:
        out["from_chat"] = {
            "chat_slug": provenance.chat_slug,
            "chat_title": provenance.chat_title,
        }
    folders = chat_context_folders or []
    if folders:
        out["chat_context_folders"] = [
            {
                "name": f.name,
                "mount_path": f.mount_path,
                "chat_slug": f.chat_slug,
                "chat_title": f.chat_title,
                "context_filename": f.context_filename,
            }
            for f in folders
        ]
    return out


def deserialize_work_record(
    data: dict[str, Any]
) -> tuple[Work, list[Context], WorkChatProvenance | None, list[WorkChatContextFolder]]:
    from_chat = _deserialize_from_chat(data.get("from_chat"))
    work = Work(
        id=data.get("id"),
        slug=data.get("slug"),
        name=data["name"],
        description=data["description"],
        status=data["status"],
        created_at=datetime.fromisoformat(data["created_at"]),
        project_slug=data.get("project_slug"),
        from_chat_slug=from_chat.chat_slug if from_chat is not None else None,
        from_chat_title=from_chat.chat_title if from_chat is not None else None,
    )
    return work, deserialize_contexts(data), from_chat, _deserialize_chat_context_folders(data)


def deserialize_contexts(data: dict[str, Any]) -> list[Context]:
    raw = data.get("contexts", [])
    return [
        Context(type=item["type"], value=item["value"], conn_id=item.get("conn_id")) for item in raw
    ]


def serialize_agent(agent: Agent, contexts: list[Context] | None = None) -> dict[str, Any]:
    """Serialise an agent for ``agent.json``. ``contexts`` is FS-only — it
    mirrors ``Work.contexts``: not on the entity, passed as a sibling."""
    out: dict[str, Any] = {
        "id": agent.id,
        "slug": agent.slug,
        "work_id": agent.work_id,
        "name": agent.name,
        "persona": agent.persona,
        "role": agent.role,
        "provider": agent.provider,
        "model": agent.model,
        "folder": str(agent.folder),
        "status": agent.status,
        "started_at": agent.started_at.isoformat(),
        "stopped_at": agent.stopped_at.isoformat() if agent.stopped_at else None,
        "session_id": agent.session_id,
        "parent_session_id": agent.parent_session_id,
        "contexts": [_serialize_context(c) for c in (contexts or [])],
    }
    # Only emit the ``options`` key when actually present, so old
    # agent.json files round-trip byte-for-byte through reconcile.
    if agent.options:
        out["options"] = dict(agent.options)
    return out


def deserialize_agent(data: dict[str, Any]) -> Agent:
    stopped_raw = data.get("stopped_at")
    options = data.get("options")
    return Agent(
        id=data.get("id"),
        slug=data.get("slug"),
        work_id=data["work_id"],
        name=data["name"],
        persona=data["persona"],
        role=data["role"],
        provider=data["provider"],
        model=data["model"],
        folder=Path(data["folder"]),
        status=data["status"],
        started_at=datetime.fromisoformat(data["started_at"]),
        stopped_at=datetime.fromisoformat(stopped_raw) if stopped_raw else None,
        session_id=data.get("session_id"),
        parent_session_id=data.get("parent_session_id"),
        options=dict(options) if options else None,
    )


def _serialize_context(c: Context) -> dict[str, Any]:
    out: dict[str, Any] = {"type": c.type, "value": c.value}
    if c.conn_id is not None:
        out["conn_id"] = c.conn_id
    return out


def _deserialize_from_chat(raw: Any) -> WorkChatProvenance | None:
    if not isinstance(raw, dict):
        return None
    chat_slug = raw.get("chat_slug") or raw.get("id")
    chat_title = raw.get("chat_title") or raw.get("title")
    if not isinstance(chat_slug, str) or not chat_slug:
        return None
    if not isinstance(chat_title, str) or not chat_title:
        chat_title = chat_slug
    return WorkChatProvenance(chat_slug=chat_slug, chat_title=chat_title)


def _deserialize_chat_context_folders(
    data: dict[str, Any]
) -> list[WorkChatContextFolder]:
    raw = data.get("chat_context_folders", [])
    if not isinstance(raw, list):
        return []
    out: list[WorkChatContextFolder] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        raw_mount_path = item.get("mount_path") or item.get("relPath")
        raw_chat_slug = item.get("chat_slug") or item.get("chatRef")
        raw_chat_title = (
            item.get("chat_title") or item.get("chatTitle") or raw_chat_slug
        )
        raw_context_filename = item.get("context_filename") or "context.md"
        if not (
            isinstance(raw_name, str)
            and raw_name
            and isinstance(raw_mount_path, str)
            and raw_mount_path
            and isinstance(raw_chat_slug, str)
            and raw_chat_slug
        ):
            continue
        chat_title = (
            raw_chat_title
            if isinstance(raw_chat_title, str) and raw_chat_title
            else raw_chat_slug
        )
        context_filename = (
            raw_context_filename
            if isinstance(raw_context_filename, str) and raw_context_filename
            else "context.md"
        )
        out.append(
            WorkChatContextFolder(
                name=raw_name,
                mount_path=raw_mount_path,
                chat_slug=raw_chat_slug,
                chat_title=chat_title,
                context_filename=context_filename,
            )
        )
    return out
