"""Scoped lookup actions for reusable loop definitions."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.loop.builtins import builtin_loop_definition, builtin_loop_definitions
from src.domain.loop.definitions import LoopDefinitionNotFound, LoopRootUnavailable
from src.domain.loop.dtos import LoopDefinition, LoopDefinitionScope
from src.domain.loop.ports import LoopDefinitionRepository


@dataclass(frozen=True)
class LoopDefinitionRoots:
    """Storage roots visible to one loop-library request."""

    library: str
    work: str | None = None


@dataclass(frozen=True)
class LocatedLoopDefinition:
    """One resolved definition and its writable root, when applicable."""

    definition: LoopDefinition
    root: str | None


def list_available_definitions(
    repository: LoopDefinitionRepository,
    roots: LoopDefinitionRoots,
) -> tuple[LoopDefinition, ...]:
    """List definitions after applying scope precedence.

    Preconditions: configured roots are trusted Atelier folders.
    Postconditions: each definition id appears once; Work overrides library,
    library overrides built-ins.
    """
    by_id = {
        definition.definition_id: definition for definition in builtin_loop_definitions()
    }
    by_id.update(
        {
            definition.definition_id: definition
            for definition in _stored(
                repository, roots.library, scope=LoopDefinitionScope.LIBRARY
            )
        }
    )
    by_id.update(
        {
            definition.definition_id: definition
            for definition in _stored(
                repository,
                roots.work,
                scope=LoopDefinitionScope.WORK,
            )
        }
    )
    rank = {
        LoopDefinitionScope.BUILTIN: 0,
        LoopDefinitionScope.LIBRARY: 1,
        LoopDefinitionScope.WORK: 2,
    }
    return tuple(
        sorted(
            by_id.values(),
            key=lambda definition: (
                rank[definition.scope],
                definition.name.casefold(),
                definition.definition_id,
            ),
        )
    )


def locate_definition(
    repository: LoopDefinitionRepository,
    roots: LoopDefinitionRoots,
    definition_id: str,
    *,
    scope: LoopDefinitionScope | None = None,
) -> LocatedLoopDefinition:
    """Resolve one definition and the root that owns it.

    Preconditions: ``definition_id`` is an external stable loop id.
    Postconditions: returns the selected scope without mutating storage.
    """
    candidates: tuple[tuple[LoopDefinitionScope, str | None], ...]
    if scope is None:
        candidates = (
            (LoopDefinitionScope.WORK, roots.work),
            (LoopDefinitionScope.LIBRARY, roots.library),
            (LoopDefinitionScope.BUILTIN, None),
        )
    elif scope == LoopDefinitionScope.WORK:
        candidates = ((scope, roots.work),)
    elif scope == LoopDefinitionScope.LIBRARY:
        candidates = ((scope, roots.library),)
    else:
        candidates = ((scope, None),)

    for candidate_scope, root in candidates:
        if candidate_scope == LoopDefinitionScope.BUILTIN:
            definition = builtin_loop_definition(definition_id)
        elif root is not None:
            definition = repository.get_definition(
                root,
                definition_id,
                scope=candidate_scope,
            )
        else:
            definition = None
        if definition is not None:
            return LocatedLoopDefinition(definition, root)
    raise LoopDefinitionNotFound(f"loop definition not found: {definition_id}")


def writable_root(
    roots: LoopDefinitionRoots,
    scope: LoopDefinitionScope,
) -> str:
    """Return the configured writable root for a non-built-in scope.

    Preconditions: ``scope`` is the requested storage ownership.
    Postconditions: returns a root or raises before persistence is attempted.
    """
    root = roots.work if scope == LoopDefinitionScope.WORK else roots.library
    if scope == LoopDefinitionScope.BUILTIN or root is None:
        raise LoopRootUnavailable(f"loop storage is unavailable for scope: {scope.value}")
    return root


def _stored(
    repository: LoopDefinitionRepository,
    root: str | None,
    *,
    scope: LoopDefinitionScope = LoopDefinitionScope.LIBRARY,
) -> tuple[LoopDefinition, ...]:
    if root is None:
        return ()
    return tuple(repository.list_definitions(root, scope=scope))


__all__ = [
    "LocatedLoopDefinition",
    "LoopDefinitionRoots",
    "list_available_definitions",
    "locate_definition",
    "writable_root",
]
