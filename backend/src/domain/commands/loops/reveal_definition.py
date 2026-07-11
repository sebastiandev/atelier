"""Resolve the filesystem directory for one repository loop definition."""

from dataclasses import dataclass
from pathlib import Path

from src.domain.commands.loops._root import WorkNotFound, resolve_working_root
from src.domain.loop.definitions import LoopDefinitionNotFound, LoopRootUnavailable
from src.domain.loop.ports import LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class RevealLoopDefinitionRequest:
    """Command input for revealing one saved repository loop."""

    work_slug: str
    definition_id: str


def execute(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    repository: LoopDefinitionRepository,
    req: RevealLoopDefinitionRequest,
) -> Path:
    """Return the trusted directory for a saved repository loop.

    Preconditions: Work, Planning root, and repository definition exist.
    Postconditions: returns a root-contained path without changing filesystem state.
    """
    root = resolve_working_root(workstore, planning_sessions, req.work_slug)
    if repository.get_definition(root, req.definition_id) is None:
        raise LoopDefinitionNotFound(
            f"repository loop definition not found: {req.definition_id}"
        )
    return Path(root).expanduser().resolve() / ".atelier" / "loops" / req.definition_id


__all__ = [
    "LoopDefinitionNotFound",
    "LoopRootUnavailable",
    "RevealLoopDefinitionRequest",
    "WorkNotFound",
    "execute",
]
