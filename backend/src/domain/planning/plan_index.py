"""Render the plan index handed to agents launched from a story."""

from __future__ import annotations

from src.domain.planning.dtos import PlanArtifactSummary, WorkPlanView


def render_plan_index(plan: WorkPlanView, story: PlanArtifactSummary) -> str:
    """Markdown index of every plan file, sources first, the story marked.

    Preconditions: ``story`` is one of ``plan.artifacts``.
    Postconditions: pure; returns text that names absolute paths only.
    """
    sources = [a for a in plan.artifacts if not a.executable]
    executables = [a for a in plan.artifacts if a.executable]
    lines = [
        "# Plan index",
        "",
        f"Plan root: `{plan.plan_artifacts_path}`",
        f"Your story: **{story.title}** (`{story.id}`) — `{story.source_ref}`",
        "",
        "`@<path>` in later messages means the file at `<plan root>/<path>`.",
        "",
        "## Sources",
    ]
    lines += [_row(a) for a in sources] or ["- (none)"]
    lines += ["", "## Stories and tasks"]
    lines += [_row(a, current=a.id == story.id) for a in executables]
    return "\n".join(lines) + "\n"


def _row(artifact: PlanArtifactSummary, *, current: bool = False) -> str:
    marker = " ← this story" if current else ""
    return f"- {artifact.kind} · {artifact.title} · `{artifact.source_ref}`{marker}"


__all__ = ["render_plan_index"]
