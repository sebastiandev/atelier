"""Filesystem adapter for loop-stage context references."""

from __future__ import annotations

from pathlib import Path

from src.domain.loop.dtos import (
    LoopContextKind,
    LoopContextReference,
    LoopContextResolution,
    LoopContextResolutionRequest,
)

_DYNAMIC = {
    LoopContextKind.WORKSPACE_DIFF,
    LoopContextKind.CHANGED_FILES,
    LoopContextKind.PREVIOUS_REPORT,
}


class FilesystemLoopContextResolver:
    """Resolve context paths below a selected repository root."""

    def resolve(self, request: LoopContextResolutionRequest) -> LoopContextResolution:
        root = request.root_path.expanduser().resolve()
        entries: list[str] = []
        missing: list[str] = []
        warnings: list[str] = []
        for reference in request.references:
            resolved = _resolve_reference(root, request, reference)
            if resolved:
                entries.extend(resolved)
                continue
            message = _missing_label(reference)
            (missing if reference.required else warnings).append(message)
        return LoopContextResolution(
            entries=tuple(dict.fromkeys(entries)),
            missing_required=tuple(missing),
            warnings=tuple(warnings),
        )


def _resolve_reference(
    root: Path,
    request: LoopContextResolutionRequest,
    reference: LoopContextReference,
) -> tuple[str, ...]:
    if reference.kind == LoopContextKind.TARGET:
        return _existing_paths(root, (request.target_ref,))
    if reference.kind == LoopContextKind.PLAN_INDEX:
        return _existing_paths(root, (request.plan_index_ref,))
    if reference.kind == LoopContextKind.ARTIFACT_DEPENDENCIES:
        return tuple(f"dependency: {item}" for item in request.dependencies)
    if reference.kind in _DYNAMIC:
        suffix = f" ({reference.step})" if reference.step else ""
        return (f"{reference.kind.value}{suffix}: resolved when the stage starts",)
    if reference.kind == LoopContextKind.FILES:
        matches: list[str] = []
        for pattern in reference.paths:
            matches.extend(
                str(path)
                for path in root.glob(pattern)
                if path.is_file() and _inside(root, path)
            )
        return tuple(sorted(set(matches)))
    if reference.kind == LoopContextKind.FOLDER:
        return tuple(
            str(path)
            for value in reference.paths
            if (path := (root / value).resolve()).is_dir() and _inside(root, path)
        )
    if reference.kind == LoopContextKind.NOTE:
        return (f"note: {reference.ref}",) if reference.ref else ()
    if reference.kind == LoopContextKind.SHARED_CONTEXT:
        return (
            (f"shared_context: {reference.ref}",)
            if reference.ref and reference.ref in request.shared_context_refs
            else ()
        )
    return ()


def _existing_paths(root: Path, values: tuple[str, ...]) -> tuple[str, ...]:
    rows: list[str] = []
    for value in values:
        path = Path(value).expanduser()
        path = path.resolve() if path.is_absolute() else (root / path).resolve()
        if path.exists() and _inside(root, path):
            rows.append(str(path))
    return tuple(rows)


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _missing_label(reference: LoopContextReference) -> str:
    detail = ", ".join(reference.paths) or reference.step or reference.ref or ""
    return f"{reference.kind.value}{f': {detail}' if detail else ''}"


__all__ = ["FilesystemLoopContextResolver"]
