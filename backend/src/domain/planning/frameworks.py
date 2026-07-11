"""Framework-specific planning behavior and repository readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from src.domain.planning.dtos import (
    PlanningDepth,
    PlanningFramework,
    PlanningFrameworkStatus,
    PlanningProfile,
)


@dataclass(frozen=True)
class PlanningRole:
    """A framework role or lens the user can invoke during Planning chat."""

    name: str
    description: str


@dataclass(frozen=True)
class PlanningFrameworkDefinition:
    """Backend-owned framework behavior for planning prompts and files."""

    id: PlanningFramework
    label: str
    description: str
    marker_paths: tuple[str, ...]
    setup_command: tuple[str, ...]
    setup_hint: str
    discovery_focus: str
    revision_focus: str
    artifact_root_template: str
    roles: tuple[PlanningRole, ...]


_FRAMEWORKS: dict[PlanningFramework, PlanningFrameworkDefinition] = {
    "bmad": PlanningFrameworkDefinition(
        id="bmad",
        label="BMAD",
        description="Role-led brief, design, architecture, and story planning.",
        marker_paths=(".bmad-core", ".bmad", "_bmad", "bmad"),
        setup_command=("npx", "bmad-method", "install"),
        setup_hint="Initialize BMAD in this working folder with `npx bmad-method install`.",
        discovery_focus=(
            "Move through brief, design guide, architecture decisions when useful, "
            "then executable stories or spikes."
        ),
        revision_focus=(
            "Use BMAD roles to refine the source-backed brief, design guide, "
            "architecture notes, and executable stories."
        ),
        artifact_root_template="_bmad-output/{work_slug}",
        roles=(
            PlanningRole("Analyst", "Clarifies problem framing, goals, and constraints."),
            PlanningRole("Product Manager", "Shapes outcomes, scope, and acceptance."),
            PlanningRole("Architect", "Surfaces source areas, dependencies, and risks."),
            PlanningRole("Scrum Master", "Slices the plan into executable stories."),
        ),
    ),
    "spec": PlanningFrameworkDefinition(
        id="spec",
        label="Spec-kit",
        description="Spec-first planning with scenarios and derived tasks.",
        marker_paths=(".specify",),
        setup_command=(
            "uvx",
            "--from",
            "git+https://github.com/github/spec-kit.git",
            "specify",
            "init",
            ".",
        ),
        setup_hint="Initialize Spec-kit in this working folder with `specify init .`.",
        discovery_focus=(
            "Start from a living spec, scenarios, acceptance criteria, constraints, "
            "and then implementation tasks."
        ),
        revision_focus=(
            "Keep the living spec, scenarios, acceptance criteria, and tasks aligned."
        ),
        artifact_root_template="specs/{work_slug}",
        roles=(
            PlanningRole("Spec Steward", "Keeps behavior and non-goals precise."),
            PlanningRole("Scenario Writer", "Expands examples, edge cases, and flows."),
            PlanningRole("Acceptance Reviewer", "Makes criteria testable."),
            PlanningRole("Implementation Planner", "Derives tasks from the spec."),
        ),
    ),
    "openspec": PlanningFrameworkDefinition(
        id="openspec",
        label="OpenSpec",
        description="Proposal/spec/task workflow with source-of-truth specs.",
        marker_paths=(".openspec",),
        setup_command=("openspec", "init"),
        setup_hint="Initialize OpenSpec in this working folder with `openspec init`.",
        discovery_focus=(
            "Frame the change as a proposal, source-of-truth spec updates, "
            "tasks, and validation."
        ),
        revision_focus=(
            "Keep OpenSpec proposal, spec deltas, tasks, and validation coherent."
        ),
        artifact_root_template=".openspec/changes/{work_slug}",
        roles=(
            PlanningRole("Proposal Author", "Frames the change and rationale."),
            PlanningRole("Spec Editor", "Maintains source-of-truth behavior."),
            PlanningRole("Task Planner", "Breaks the approved change into tasks."),
            PlanningRole("Validation Reviewer", "Checks coverage and readiness."),
        ),
    ),
    "custom": PlanningFrameworkDefinition(
        id="custom",
        label="Custom",
        description="User-defined planning structure.",
        marker_paths=(),
        setup_command=(),
        setup_hint="Custom planning does not require a repo-local framework install.",
        discovery_focus=(
            "Ask which artifacts, roles, review gates, and depth the user wants "
            "before committing to a plan shape."
        ),
        revision_focus="Refine the user-selected source-backed planning artifacts.",
        artifact_root_template="planning/{work_slug}",
        roles=(
            PlanningRole("Artifact Elicitor", "Asks which docs and gates are needed."),
            PlanningRole("Scope Reviewer", "Checks that the chosen structure is enough."),
            PlanningRole("Plan Writer", "Maintains the chosen artifact set."),
        ),
    ),
}


def framework_definition(
    framework: PlanningFramework | None,
) -> PlanningFrameworkDefinition:
    """Return the framework definition, defaulting to BMAD."""
    return _FRAMEWORKS.get(framework or "bmad", _FRAMEWORKS["bmad"])


def artifact_root_rel_path(
    framework: PlanningFramework | None, work_slug: str
) -> str:
    """Return where framework-generated planning files should live.

    Preconditions: ``work_slug`` is the canonical Work slug.
    Postconditions: returns a safe POSIX path relative to the selected work root.
    """
    definition = framework_definition(framework)
    return _safe_rel_path(
        definition.artifact_root_template.format(work_slug=_safe_segment(work_slug))
    )


def check_framework_status(
    framework: PlanningFramework | None, root_path: str
) -> PlanningFrameworkStatus:
    """Check whether the selected framework is initialized in ``root_path``.

    Preconditions: ``root_path`` points at the user's selected working folder.
    Postconditions: no filesystem state is changed.
    """
    definition = framework_definition(framework)
    root = Path(root_path).expanduser()
    if definition.id == "custom":
        return PlanningFrameworkStatus(
            framework=definition.id,
            label=definition.label,
            root_path=str(root),
            ready=root.exists() and root.is_dir(),
            markers=[],
            setup_command=[],
            setup_hint=definition.setup_hint,
        )
    markers = [
        marker for marker in definition.marker_paths if (root / marker).exists()
    ]
    return PlanningFrameworkStatus(
        framework=definition.id,
        label=definition.label,
        root_path=str(root),
        ready=root.exists() and root.is_dir() and bool(markers),
        markers=markers,
        setup_command=list(definition.setup_command),
        setup_hint=definition.setup_hint,
    )


def depth_for_profile(profile: PlanningProfile) -> PlanningDepth:
    """Return the default planning depth for a profile."""
    if profile in ("bugfix", "hotfix"):
        return "minimal"
    if profile in ("refactor", "migration"):
        return "standard"
    if profile == "full_app":
        return "deep"
    if profile == "custom":
        return "custom"
    return "lightweight"


def _safe_segment(value: str) -> str:
    segment = value.strip()
    rel = PurePosixPath(segment)
    if (
        not segment
        or rel.is_absolute()
        or len(rel.parts) != 1
        or rel.parts[0] in {"", ".", ".."}
        or "\x00" in segment
    ):
        raise ValueError(f"invalid framework path segment: {value!r}")
    return segment


def _safe_rel_path(value: str) -> str:
    rel = PurePosixPath(value)
    if (
        not value
        or rel.is_absolute()
        or any(part in {"", ".", ".."} for part in rel.parts)
        or "\x00" in value
    ):
        raise ValueError(f"invalid framework output path: {value!r}")
    return rel.as_posix()


__all__ = [
    "PlanningFrameworkDefinition",
    "PlanningRole",
    "artifact_root_rel_path",
    "check_framework_status",
    "depth_for_profile",
    "framework_definition",
]
