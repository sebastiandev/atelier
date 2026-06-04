"""Request/response DTOs for the WorkStore port.

All input DTOs are frozen so command callers cannot mutate them after
dispatch. `WorkRecord` bundles a `Work` with its contexts because contexts
live in `work.json` (filesystem) rather than SQLite — `get_work` is the
one place a caller wants both halves together.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
class WorkChatProvenance:
    chat_slug: str
    chat_title: str


@dataclass(frozen=True)
class WorkChatContextFolder:
    name: str
    mount_path: str
    chat_slug: str
    chat_title: str
    context_filename: str = "context.md"
    # Populated on read after the filesystem adapter resolves the
    # canonical work-scoped folder path.
    absolute_path: Path | None = None


@dataclass(frozen=True)
class CreateWorkChatContextFolder:
    name: str
    mount_path: str
    chat_slug: str
    chat_title: str
    context_markdown: str
    context_filename: str = "context.md"


@dataclass(frozen=True)
class CreateWorkRequest:
    name: str
    description: str
    contexts: list[Context] = field(default_factory=list)
    # Optional grouping. ``None`` is "loose work" — first-class, not a
    # hidden bucket. Validated by the route layer (project must exist).
    project_slug: str | None = None
    from_chat: WorkChatProvenance | None = None
    chat_context_folders: list[CreateWorkChatContextFolder] = field(
        default_factory=list
    )


@dataclass(frozen=True)
class UpdateWorkRequest:
    """Partial update — fields left as ``None`` are not changed."""

    work_slug: str
    name: str | None = None
    description: str | None = None
    status: WorkStatus | None = None
    contexts: list[Context] | None = None


@dataclass(frozen=True)
class EnsureWorkChatContextRequest:
    work_slug: str
    folder: CreateWorkChatContextFolder


@dataclass(frozen=True)
class WorkRecord:
    work: Work
    contexts: list[Context]
    from_chat: WorkChatProvenance | None = None
    chat_context_folders: list[WorkChatContextFolder] = field(
        default_factory=list
    )


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
    # Provider-specific options validated by the Spec (``permission_mode``,
    # ``thinking_effort``, ``custom_allowed_tools``, …). Persisted on the
    # agent row so resume + detach see the same selections later. Empty
    # dict means "use provider defaults" — same as ``None`` post-load.
    options: dict[str, Any] = field(default_factory=dict)


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
    "CreateWorkChatContextFolder",
    "CreateWorkRequest",
    "EnsureWorkChatContextRequest",
    "RecordArtifactRequest",
    "RecordHandoffRequest",
    "UpdateWorkRequest",
    "WorkChatContextFolder",
    "WorkChatProvenance",
    "WorkRecord",
]
