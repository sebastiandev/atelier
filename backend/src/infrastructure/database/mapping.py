"""Imperative SQLAlchemy mapping between domain entities and tables.

Domain entities live in `src.domain.models` as plain dataclasses with no
SQLAlchemy imports. This module is the only place where the binding happens —
keeping `domain/` framework-free per the project's clean-architecture rule.

Idempotent: `configure_mappings()` may be called multiple times safely.
"""

from sqlalchemy.orm import registry

from src.domain.artifacts.models import (
    BaseArtifact,
    DocArtifact,
    JiraArtifact,
    PrArtifact,
)
from src.domain.models import (
    Agent,
    Chat,
    Connection,
    Handoff,
    Project,
    SharedFolder,
    Work,
)
from src.infrastructure.database.tables import (
    agents_table,
    artifacts_table,
    chats_table,
    connections_table,
    handoffs_table,
    projects_table,
    shared_folders_table,
    works_table,
)

mapper_registry = registry()

_configured = False


def configure_mappings() -> None:
    """Bind domain dataclasses to SQLAlchemy tables. Safe to call repeatedly.

    `Context` is intentionally not mapped — contexts live in `work.json` on the
    filesystem, not in SQLite. Same for transcript events (NDJSON) and the
    schema_version stamp (handled directly by `migrations.py`).
    """
    global _configured
    if _configured:
        return

    mapper_registry.map_imperatively(Project, projects_table)
    mapper_registry.map_imperatively(Work, works_table)
    mapper_registry.map_imperatively(Chat, chats_table)
    mapper_registry.map_imperatively(Agent, agents_table)
    # Artifact: single-table inheritance on the existing ``artifacts``
    # table. The ``type`` column is the polymorphic discriminator;
    # subclasses pick up the columns they actually use via ``properties``.
    # ``polymorphic_identity`` on the base is None — it's abstract; only
    # the subclasses are instantiable polymorphic targets.
    mapper_registry.map_imperatively(
        BaseArtifact,
        artifacts_table,
        polymorphic_on=artifacts_table.c.type,
    )
    mapper_registry.map_imperatively(
        PrArtifact,
        inherits=BaseArtifact,
        polymorphic_identity="pr",
    )
    mapper_registry.map_imperatively(
        JiraArtifact,
        inherits=BaseArtifact,
        polymorphic_identity="jira",
    )
    mapper_registry.map_imperatively(
        DocArtifact,
        inherits=BaseArtifact,
        polymorphic_identity="doc",
    )
    mapper_registry.map_imperatively(Connection, connections_table)
    mapper_registry.map_imperatively(Handoff, handoffs_table)
    mapper_registry.map_imperatively(SharedFolder, shared_folders_table)

    _configured = True
