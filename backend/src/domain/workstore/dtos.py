"""Request/response DTOs for the WorkStore port.

All input DTOs are frozen so command callers cannot mutate them after
dispatch. `WorkRecord` bundles a `Work` with its contexts because contexts
live in `work.json` (filesystem) rather than SQLite — `get_work` is the
one place a caller wants both halves together.
"""

from dataclasses import dataclass, field
from pathlib import Path

from src.domain.models import (
    ArtifactType,
    Context,
    HandoffTargetDialog,
    Persona,
    Provider,
    Work,
    WorkStatus,
)


@dataclass(frozen=True)
class CreateWorkRequest:
    name: str
    description: str
    contexts: list[Context] = field(default_factory=list)
    # Optional grouping. ``None`` is "loose work" — first-class, not a
    # hidden bucket. Validated by the route layer (project must exist).
    project_slug: str | None = None


@dataclass(frozen=True)
class UpdateWorkRequest:
    """Partial update — fields left as ``None`` are not changed."""

    work_slug: str
    name: str | None = None
    description: str | None = None
    status: WorkStatus | None = None
    contexts: list[Context] | None = None


@dataclass(frozen=True)
class WorkRecord:
    work: Work
    contexts: list[Context]


@dataclass(frozen=True)
class AddAgentRequest:
    work_slug: str
    name: str
    persona: Persona
    role: str
    provider: Provider
    model: str
    folder: Path
    contexts: tuple[Context, ...] = ()


@dataclass(frozen=True)
class RecordArtifactRequest:
    work_slug: str
    type: ArtifactType
    title: str
    status: str
    agent_slug: str | None = None
    repo: str | None = None
    url: str | None = None
    doc_path: str | None = None


@dataclass(frozen=True)
class RecordHandoffRequest:
    work_slug: str
    source_agent_slug: str
    doc_text: str
    doc_filename: str
    target_agent_slug: str | None = None
    target_dialog: HandoffTargetDialog | None = None


__all__ = [
    "AddAgentRequest",
    "CreateWorkRequest",
    "RecordArtifactRequest",
    "RecordHandoffRequest",
    "UpdateWorkRequest",
    "WorkRecord",
]
