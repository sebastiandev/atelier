"""Working-root actions shared by loop definition commands."""

from src.domain.loop.catalog import LoopDefinitionRoots
from src.domain.loop.definitions import LoopRootUnavailable
from src.domain.loop.ports import LoopDefinitionLocations, LoopWorkingRootRepository
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The requested Work does not exist."""


def resolve_working_root(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    work_slug: str,
) -> str:
    """Resolve a Work's persisted repository root.

    Preconditions: the caller is operating on a Work-scoped loop resource.
    Postconditions: returns the configured Work root without changing state.
    """
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    root = work_roots.working_root_for_work(work_slug)
    if not root:
        raise LoopRootUnavailable(f"loop working root is not configured: {work_slug}")
    return root


def resolve_catalog_roots(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    *,
    work_slug: str | None,
    root_path: str | None,
) -> LoopDefinitionRoots:
    """Resolve global, Work-local, and legacy loop roots.

    Preconditions: ``root_path`` is either absent or selected by the local user.
    Postconditions: an existing Work is required when ``work_slug`` is provided;
    no filesystem content is changed.
    """
    work_root: str | None = None
    legacy_root = root_path.strip() if root_path and root_path.strip() else None
    if work_slug is not None:
        if workstore.get_work(work_slug) is None:
            raise WorkNotFound(f"work not found: {work_slug}")
        work_root = locations.work_loop_root(work_slug)
        if legacy_root is None:
            legacy_root = work_roots.working_root_for_work(work_slug)
    return LoopDefinitionRoots(
        library=locations.loop_library_root(),
        work=work_root,
        legacy=legacy_root,
    )


__all__ = ["WorkNotFound", "resolve_catalog_roots", "resolve_working_root"]
