"""Filesystem JSON/NDJSON shape for chats."""

from datetime import datetime
from typing import Any

from src.domain.chatstore.dtos import ChatGrounding
from src.domain.models import Chat, ChatMessage


def serialize_chat(chat: Chat) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": chat.id,
        "slug": chat.slug,
        "title": chat.title,
        "provider": chat.provider,
        "model": chat.model,
        "created_at": chat.created_at.isoformat(),
        "updated_at": chat.updated_at.isoformat(),
        "promoted_to_work_slug": chat.promoted_to_work_slug,
    }
    if chat.session_id is not None:
        out["session_id"] = chat.session_id
    if chat.working_directory:
        out["working_directory"] = chat.working_directory
    if chat.grounding_kind and chat.grounding_ref:
        out["grounding"] = {
            "kind": chat.grounding_kind,
            "ref": chat.grounding_ref,
        }
    return out


def deserialize_chat(data: dict[str, Any]) -> Chat:
    grounding = deserialize_grounding(data.get("grounding"))
    return Chat(
        id=data.get("id"),
        slug=data.get("slug"),
        title=data["title"],
        provider=data["provider"],
        model=data["model"],
        grounding_kind=grounding.kind if grounding else None,
        grounding_ref=grounding.ref if grounding else None,
        working_directory=data.get("working_directory"),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
        session_id=data.get("session_id"),
        promoted_to_work_slug=data.get("promoted_to_work_slug"),
    )


def deserialize_grounding(raw: Any) -> ChatGrounding | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    ref = raw.get("ref") or raw.get("id") or raw.get("path")
    if kind not in ("project", "work", "folder"):
        return None
    if not isinstance(ref, str) or not ref:
        return None
    return ChatGrounding(kind=kind, ref=ref)


def serialize_message(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "body": message.body,
        "created_at": message.created_at.isoformat(),
    }


def deserialize_message(data: dict[str, Any]) -> ChatMessage | None:
    role = data.get("role")
    body = data.get("body")
    created_at = data.get("created_at")
    if role in ("user", "assistant") and isinstance(body, str) and isinstance(created_at, str):
        return ChatMessage(
            role=role,
            body=body,
            created_at=datetime.fromisoformat(created_at),
        )

    event_type = data.get("type")
    ts = data.get("ts")
    if event_type == "user_input":
        body = data.get("text")
        if not isinstance(body, str) or not isinstance(ts, str):
            return None
        return ChatMessage(
            role="user",
            body=body,
            created_at=datetime.fromisoformat(ts),
        )
    elif event_type == "message_complete":
        body = data.get("text")
        if not isinstance(body, str) or not isinstance(ts, str):
            return None
        return ChatMessage(
            role="assistant",
            body=body,
            created_at=datetime.fromisoformat(ts),
        )
    else:
        return None


__all__ = [
    "deserialize_chat",
    "deserialize_grounding",
    "deserialize_message",
    "serialize_chat",
    "serialize_message",
]
