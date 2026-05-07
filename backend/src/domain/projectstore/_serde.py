"""JSON shape of project.json.

Shared between ``ProjectStoreService`` (writes) and ``reconcile`` (reads).
Centralising the encoder/decoder here keeps the on-disk schema in one
place — change it and both ends update together.
"""

from datetime import datetime
from typing import Any

from src.domain.models import Project


def serialize_project_record(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "glyph": project.glyph,
        "color": project.color,
        "pinned": project.pinned,
        "default_jira_conn": project.default_jira_conn,
        "default_sentry_conn": project.default_sentry_conn,
        "created_at": project.created_at.isoformat(),
    }


def deserialize_project_record(data: dict[str, Any]) -> Project:
    return Project(
        id=data.get("id"),
        slug=data.get("slug"),
        name=data["name"],
        description=data["description"],
        glyph=data["glyph"],
        color=int(data["color"]),
        pinned=bool(data.get("pinned", False)),
        default_jira_conn=data.get("default_jira_conn"),
        default_sentry_conn=data.get("default_sentry_conn"),
        created_at=datetime.fromisoformat(data["created_at"]),
    )
