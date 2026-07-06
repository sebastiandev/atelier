"""Source-backed lightweight BMAD planning service."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any, Literal

from src.domain.loop.dtos import LoopStatus
from src.domain.planning.dtos import (
    PlanArtifactDetail,
    PlanArtifactKind,
    PlanArtifactProposal,
    PlanArtifactRun,
    PlanArtifactStatus,
    PlanArtifactSummary,
    PlanningDepth,
    PlanningFramework,
    PlanningProfile,
    PlanOverview,
    PlanRunStatus,
    PlanTrackingLink,
    WorkPlanView,
)
from src.domain.planning.loop import loop_status_for_run_status
from src.domain.planning.ports import PlanningFiles

_ROOT_DOCS: tuple[tuple[str, tuple[str, ...], PlanArtifactKind, str], ...] = (
    ("intent.md", (), "brief", "Intent"),
    ("design-guide.md", ("design-guidance.md",), "architecture", "Design guide"),
    ("architecture.md", (), "architecture", "Architecture"),
    ("spec.md", (), "spec", "Spec"),
    ("scenarios.md", (), "scenario", "Scenarios"),
    ("acceptance.md", (), "acceptance", "Acceptance"),
    ("proposal.md", (), "brief", "Proposal"),
    ("specs/change.md", (), "spec", "Spec change"),
)
_EXECUTABLE_DIRS: dict[str, PlanArtifactKind] = {
    "stories": "story",
    "tasks": "task",
    "spikes": "spike",
    "bugs": "bug",
    "hotfixes": "hotfix",
}
_ARTIFACT_KINDS: set[str] = {
    "brief",
    "architecture",
    "spec",
    "scenario",
    "acceptance",
    "story",
    "task",
    "spike",
    "bug",
    "hotfix",
    "note",
}
_EXECUTABLE_KINDS: set[str] = {"story", "task", "spike", "bug", "hotfix"}
_PROFILES: set[str] = {
    "feature",
    "refactor",
    "migration",
    "bugfix",
    "hotfix",
    "full_app",
    "custom",
}
_RUN_STATUSES: set[str] = {
    "running",
    "needs_attention",
    "waiting_approval",
    "blocked",
    "completed_pending_review",
    "accepted",
}
_LOOP_STATUSES: set[str] = {
    "pending",
    "running",
    "waiting_report",
    "assessing",
    "needs_agent",
    "blocked_user",
    "completed",
    "failed",
    "cancelled",
}
_PROPOSAL_STATUSES: set[str] = {"pending", "accepted", "rejected"}
_TRACKING_KINDS: set[str] = {"jira", "pr", "blocker", "bug"}


class PlanningNotStarted(ValueError):
    """Planning has not been started for this work."""


class PlanArtifactNotFound(ValueError):
    """The requested plan artifact does not exist in the current index."""


class PlanArtifactConflict(ValueError):
    """The source file changed after the caller loaded it."""


class PlanArtifactNotExecutable(ValueError):
    """The requested plan artifact is not executable work."""


class PlanArtifactProposalNotFound(ValueError):
    """The requested source proposal does not exist."""


class PlanArtifactRunNotFound(ValueError):
    """The requested artifact run does not exist."""


class PlanningService:
    """Source-backed plan projection helper."""

    def __init__(self, files: PlanningFiles) -> None:
        self._files = files

    def get_plan(self, work_slug: str) -> WorkPlanView | None:
        """Index source files into the normalized Plan View.

        Preconditions: none; missing manifest returns ``None``.
        Postconditions: no source files are changed.
        """
        manifest = self._files.read_manifest(work_slug)
        if manifest is None:
            return None
        root_path = self._files.working_root(work_slug)
        if root_path is None:
            return None
        artifacts = self._index_artifacts(work_slug, manifest)
        profile = _profile(manifest.get("profile"), "feature")
        return WorkPlanView(
            work_slug=work_slug,
            framework=_framework(manifest.get("framework"), "bmad"),
            profile=profile,
            phase=_phase(manifest.get("phase")),
            depth=_depth(manifest.get("depth"), _depth_for_profile(profile)),
            root_path=root_path,
            planning_path=self._files.planning_path(work_slug),
            approved_at=_str_or_none(manifest.get("approved_at")),
            stale=_is_stale(artifacts, manifest),
            artifacts=artifacts,
            overview=_overview(artifacts),
        )

    def get_artifact(
        self, work_slug: str, artifact_id: str
    ) -> PlanArtifactDetail | None:
        """Read one indexed artifact.

        Preconditions: planning is started.
        Postconditions: no source files are changed.
        """
        manifest = self._manifest_or_raise(work_slug)
        artifact = self._artifact_or_none(work_slug, manifest, artifact_id)
        if artifact is None:
            return None
        content = self._files.read_text(work_slug, artifact.path)
        if content is None:
            return None
        return PlanArtifactDetail(artifact=artifact, content=content)

    def _manifest_or_raise(self, work_slug: str) -> dict[str, Any]:
        manifest = self._files.read_manifest(work_slug)
        if manifest is None:
            raise PlanningNotStarted(f"planning not started: {work_slug}")
        return manifest

    def _artifact_or_none(
        self, work_slug: str, manifest: dict[str, Any], artifact_id: str
    ) -> PlanArtifactSummary | None:
        for artifact in self._index_artifacts(work_slug, manifest):
            if artifact.id == artifact_id:
                return artifact
        return None

    def _index_artifacts(
        self, work_slug: str, manifest: dict[str, Any]
    ) -> list[PlanArtifactSummary]:
        manifest_entries = _artifact_entries_from_manifest(manifest)
        if manifest_entries:
            rows = self._index_manifest_artifacts(work_slug, manifest_entries, manifest)
            seen_paths = {row.path for row in rows}
            seen_ids = {row.id for row in rows}
            for legacy in self._index_legacy_artifacts(work_slug, manifest):
                if legacy.path in seen_paths or legacy.id in seen_ids:
                    continue
                rows.append(legacy)
                seen_paths.add(legacy.path)
                seen_ids.add(legacy.id)
            return _with_launch_gating(rows, manifest)
        return _with_launch_gating(self._index_legacy_artifacts(work_slug, manifest), manifest)

    def _index_manifest_artifacts(
        self,
        work_slug: str,
        entries: list[dict[str, Any]],
        manifest: dict[str, Any],
    ) -> list[PlanArtifactSummary]:
        staged: list[tuple[dict[str, Any], str, str]] = []
        path_to_id: dict[str, str] = {}
        for entry in entries:
            rel_path = entry["path"]
            content = self._read_text(work_slug, rel_path)
            if content is None:
                continue
            artifact_id = _artifact_id(rel_path, entry["artifact_kind"])
            path_to_id[rel_path] = artifact_id
            staged.append((entry, content, artifact_id))

        rows: list[PlanArtifactSummary] = []
        for entry, content, _artifact_id_value in staged:
            rel_path = entry["path"]
            kind = entry["artifact_kind"]
            rows.append(
                self._artifact_from_source(
                    work_slug,
                    manifest,
                    rel_path,
                    kind,
                    entry["title"],
                    content,
                    prefer_heading=False,
                    executable=entry["executable"],
                    dependencies=_resolve_manifest_dependencies(
                        entry["dependencies"], path_to_id
                    ),
                )
            )
        return rows

    def _index_legacy_artifacts(
        self, work_slug: str, manifest: dict[str, Any]
    ) -> list[PlanArtifactSummary]:
        rows: list[PlanArtifactSummary] = []
        for rel_path, legacy_paths, kind, fallback_title in _ROOT_DOCS:
            rel_path = self._existing_root_doc(work_slug, rel_path, legacy_paths)
            content = self._read_text(work_slug, rel_path)
            if content is None:
                continue
            rows.append(
                self._artifact_from_source(
                    work_slug,
                    manifest,
                    rel_path,
                    kind,
                    fallback_title,
                    content,
                )
            )
        for rel_dir, kind in _EXECUTABLE_DIRS.items():
            for rel_path in self._files.list_markdown(work_slug, rel_dir):
                content = self._read_text(work_slug, rel_path)
                if content is None:
                    continue
                rows.append(
                    self._artifact_from_source(
                        work_slug,
                        manifest,
                        rel_path,
                        kind,
                        kind.title(),
                        content,
                    )
                )
        return rows

    def _existing_root_doc(
        self, work_slug: str, rel_path: str, legacy_paths: tuple[str, ...]
    ) -> str:
        if self._read_text(work_slug, rel_path) is not None:
            return rel_path
        for legacy in legacy_paths:
            if self._read_text(work_slug, legacy) is not None:
                return legacy
        return rel_path

    def _read_text(self, work_slug: str, rel_path: str) -> str | None:
        try:
            return self._files.read_text(work_slug, rel_path)
        except ValueError:
            return None

    def _artifact_from_source(
        self,
        work_slug: str,
        manifest: dict[str, Any],
        rel_path: str,
        kind: PlanArtifactKind,
        fallback_title: str,
        content: str,
        *,
        prefer_heading: bool = True,
        executable: bool | None = None,
        dependencies: list[str] | None = None,
    ) -> PlanArtifactSummary:
        source_hash = _hash_text(content)
        artifact_id = _artifact_id(rel_path, kind)
        accepted = _dict(manifest.get("accepted_artifacts")).get(artifact_id)
        accepted_summary_path = (
            _str_or_none(accepted.get("summary_path")) if isinstance(accepted, dict) else None
        )
        status = _status_for(artifact_id, rel_path, source_hash, manifest)
        return PlanArtifactSummary(
            id=artifact_id,
            kind=kind,
            title=(_heading(content) if prefer_heading else None) or fallback_title,
            path=rel_path,
            source_ref=self._files.absolute_path(work_slug, rel_path),
            source_hash=source_hash,
            status=status,
            readiness=_readiness(kind, content),
            executable=executable if executable is not None else kind in _EXECUTABLE_KINDS,
            launchable=False,
            dependencies=dependencies if dependencies is not None else _dependencies(content),
            runs=_runs(manifest, artifact_id, self._files, work_slug),
            proposals=_proposals(manifest, artifact_id),
            tracking=_tracking(manifest, artifact_id),
            accepted_summary_path=(
                self._files.absolute_path(work_slug, accepted_summary_path)
                if accepted_summary_path
                else None
            ),
        )

def _artifact_id(rel_path: str, kind: PlanArtifactKind) -> str:
    if rel_path == "intent.md":
        return "brief"
    if rel_path in {"design-guide.md", "design-guidance.md"}:
        return "architecture"
    return rel_path.rsplit("/", 1)[-1].removesuffix(".md") or kind


def _artifact_entries_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("artifacts")
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = _str_or_none(item.get("path"))
        title = _str_or_none(item.get("title"))
        kind = item.get("artifact_kind")
        if (
            path is None
            or not _valid_artifact_path(path)
            or title is None
            or not isinstance(kind, str)
            or kind not in _ARTIFACT_KINDS
        ):
            continue
        dependencies = item.get("dependencies")
        entries.append(
            {
                "path": path,
                "title": title.strip() or path.rsplit("/", 1)[-1].removesuffix(".md"),
                "artifact_kind": kind,
                "executable": (
                    item["executable"]
                    if isinstance(item.get("executable"), bool)
                    else kind in _EXECUTABLE_KINDS
                ),
                "dependencies": [
                    dep.strip()
                    for dep in dependencies
                    if isinstance(dep, str) and dep.strip()
                ]
                if isinstance(dependencies, list)
                else [],
            }
        )
    return entries


def _valid_artifact_path(path: str) -> bool:
    if "\x00" in path or "\\" in path or not path.endswith(".md"):
        return False
    rel = PurePosixPath(path)
    return not rel.is_absolute() and all(part not in {"", ".", ".."} for part in rel.parts)


def _resolve_manifest_dependencies(
    dependencies: list[str], path_to_id: dict[str, str]
) -> list[str]:
    resolved: list[str] = []
    for dep in dependencies:
        dep = dep.strip().strip("`")
        if not dep or dep.lower() in {"none", "n/a", "na"}:
            continue
        resolved.append(path_to_id.get(dep, _dependency_id(dep)))
    return resolved


def _dependency_id(value: str) -> str:
    if value.endswith(".md") or "/" in value:
        return value.rsplit("/", 1)[-1].removesuffix(".md").lower()
    return value.lower()


def _status_for(
    artifact_id: str, rel_path: str, source_hash: str, manifest: dict[str, Any]
) -> PlanArtifactStatus:
    accepted = _dict(manifest.get("accepted_artifacts")).get(artifact_id)
    if isinstance(accepted, dict) and accepted.get("source_hash") == source_hash:
        return "accepted"
    approved = _dict(manifest.get("approved_source_hashes"))
    if not approved:
        return "draft"
    return "approved" if approved.get(rel_path) == source_hash else "changed"


def _readiness(kind: PlanArtifactKind, content: str) -> Literal["ready", "needs_detail"]:
    if kind not in _EXECUTABLE_KINDS:
        return "ready"
    lowered = content.lower()
    if kind in {"bug", "hotfix"}:
        if (
            "## explanation" in lowered
            and "## references" in lowered
            and "## validation" in lowered
        ):
            return "ready"
        return "needs_detail"
    has_acceptance = "## acceptance criteria" in lowered
    has_scope = any(
        heading in lowered
        for heading in ("## scope", "## objective", "## goal")
    )
    if has_acceptance and has_scope:
        return "ready"
    return "needs_detail"


def _overview(artifacts: list[PlanArtifactSummary]) -> PlanOverview:
    return PlanOverview(
        total=len(artifacts),
        ready=sum(1 for a in artifacts if a.readiness == "ready"),
        needs_detail=sum(1 for a in artifacts if a.readiness == "needs_detail"),
        approved=sum(1 for a in artifacts if a.status == "approved"),
        changed=sum(1 for a in artifacts if a.status == "changed"),
        accepted=sum(1 for a in artifacts if a.status == "accepted"),
        executable=sum(1 for a in artifacts if a.executable),
        running=sum(1 for a in artifacts if _latest_run_status(a) == "running"),
        review=sum(
            1 for a in artifacts if _latest_run_status(a) == "completed_pending_review"
        ),
        blocked=sum(1 for a in artifacts if a.executable and not a.launchable),
    )


def _is_stale(
    artifacts: list[PlanArtifactSummary], manifest: dict[str, Any]
) -> bool:
    approved = _dict(manifest.get("approved_source_hashes"))
    if not approved:
        return False
    return any(approved.get(row.path) != row.source_hash for row in artifacts)


def _with_launch_gating(
    artifacts: list[PlanArtifactSummary], manifest: dict[str, Any]
) -> list[PlanArtifactSummary]:
    by_id = {artifact.id: artifact for artifact in artifacts}
    return [
        replace(
            artifact,
            launch_blockers=_launch_blockers(artifact, by_id, manifest),
            launchable=artifact.executable
            and artifact.readiness == "ready"
            and not _launch_blockers(artifact, by_id, manifest),
        )
        for artifact in artifacts
    ]


def _launch_blockers(
    artifact: PlanArtifactSummary,
    by_id: dict[str, PlanArtifactSummary],
    manifest: dict[str, Any],
) -> list[str]:
    if not artifact.executable:
        return []
    blockers: list[str] = []
    if artifact.status == "draft" and not _dict(manifest.get("approved_source_hashes")):
        blockers.append("Approve the latest plan before launching agents.")
    if artifact.status == "changed":
        blockers.append("Approve the latest source changes before launching.")
    if artifact.readiness == "needs_detail":
        blockers.append("Add required scope, explanation, or validation detail.")
    for dep_id in artifact.dependencies:
        dep = by_id.get(dep_id)
        if dep is None:
            blockers.append(f"Dependency {dep_id} is missing.")
            continue
        if not _dependency_satisfied(dep):
            blockers.append(f"Dependency {dep_id} is not complete or ready for review.")
    for link in _tracking(manifest, artifact.id):
        if link.kind != "blocker":
            continue
        ref_id = link.ref
        if not ref_id:
            blockers.append(f"Blocked by {link.title}.")
            continue
        dep = by_id.get(ref_id)
        if dep is None:
            blockers.append(f"Blocker {ref_id} is missing.")
            continue
        if not _dependency_satisfied(dep):
            blockers.append(f"Blocked by {ref_id} until it is complete or ready for review.")
    return blockers


def _dependency_satisfied(artifact: PlanArtifactSummary) -> bool:
    """Return whether an artifact dependency is sufficiently reviewed."""
    if not artifact.executable:
        return artifact.status in {"approved", "accepted"}
    return artifact.status == "accepted" or _latest_run_status(artifact) in {
        "completed_pending_review",
        "accepted",
    }


def _dependencies(content: str) -> list[str]:
    section = _section(content, "dependencies")
    if section is None:
        return []
    deps: list[str] = []
    for raw in section.splitlines():
        line = raw.strip().lstrip("-*0123456789. ").strip("` ")
        if not line or line.lower() in {"none", "n/a", "na"}:
            continue
        match = re.search(r"\b(?:story|spike|bug|hotfix)-[a-z0-9._-]+\b", line, re.I)
        deps.append((match.group(0) if match else line.split()[0]).lower())
    return deps


def _section(content: str, name: str) -> str | None:
    pattern = re.compile(rf"^##\s+{re.escape(name)}\s*$", re.I | re.M)
    match = pattern.search(content)
    if match is None:
        return None
    start = match.end()
    next_heading = re.search(r"^##\s+", content[start:], re.M)
    end = start + next_heading.start() if next_heading else len(content)
    return content[start:end].strip()


def _runs(
    manifest: dict[str, Any],
    artifact_id: str,
    files: PlanningFiles,
    work_slug: str,
) -> list[PlanArtifactRun]:
    raw = _dict(manifest.get("artifact_runs")).get(artifact_id)
    if not isinstance(raw, list):
        return []
    out: list[PlanArtifactRun] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        agent_slug = _str_or_none(item.get("agent_slug"))
        started_at = _str_or_none(item.get("started_at"))
        if not agent_slug or not started_at:
            continue
        status = item.get("status")
        if not isinstance(status, str) or status not in _RUN_STATUSES:
            status = "running"
        loop = _dict(item.get("loop"))
        loop_status = _loop_status(loop.get("status"), status)  # type: ignore[arg-type]
        report_path = _str_or_none(item.get("report_path"))
        out.append(
            PlanArtifactRun(
                agent_slug=agent_slug,
                status=status,  # type: ignore[arg-type]
                started_at=started_at,
                completed_at=_str_or_none(item.get("completed_at")),
                cleanup_at=_str_or_none(item.get("cleanup_at")),
                report_path=(
                    files.absolute_path(work_slug, report_path) if report_path else None
                ),
                summary=_str_or_empty(item.get("summary")),
                divergences=_str_or_empty(item.get("divergences")),
                skipped_scope=_str_or_empty(item.get("skipped_scope")),
                blockers=_str_or_empty(item.get("blockers")),
                decisions=_str_or_empty(item.get("decisions")),
                changes=_str_or_empty(item.get("changes")),
                validation_evidence=_str_or_empty(item.get("validation_evidence")),
                loop_status=loop_status,
                loop_status_reason=_str_or_empty(loop.get("status_reason")),
                loop_attempt=_int_or_default(loop.get("attempt"), 1),
                loop_latest_assessment=_str_list(loop.get("findings")),
            )
        )
    return out


def _proposals(
    manifest: dict[str, Any], artifact_id: str
) -> list[PlanArtifactProposal]:
    raw = _dict(manifest.get("artifact_proposals")).get(artifact_id)
    if not isinstance(raw, list):
        return []
    out: list[PlanArtifactProposal] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        proposal_id = _str_or_none(item.get("id"))
        title = _str_or_none(item.get("title"))
        path = _str_or_none(item.get("path"))
        source_hash = _str_or_none(item.get("source_hash"))
        proposed_content = _str_or_none(item.get("proposed_content"))
        created_at = _str_or_none(item.get("created_at"))
        if (
            proposal_id is None
            or title is None
            or path is None
            or source_hash is None
            or proposed_content is None
            or created_at is None
        ):
            continue
        status = item.get("status")
        if not isinstance(status, str) or status not in _PROPOSAL_STATUSES:
            status = "pending"
        out.append(
            PlanArtifactProposal(
                id=proposal_id,
                artifact_id=artifact_id,
                title=title,
                path=path,
                source_hash=source_hash,
                proposed_content=proposed_content,
                status=status,  # type: ignore[arg-type]
                created_at=created_at,
                resolved_at=_str_or_none(item.get("resolved_at")),
            )
        )
    return out


def _tracking(manifest: dict[str, Any], artifact_id: str) -> list[PlanTrackingLink]:
    raw = _dict(manifest.get("artifact_tracking")).get(artifact_id)
    if not isinstance(raw, list):
        return []
    out: list[PlanTrackingLink] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        link_id = _str_or_none(item.get("id"))
        title = _str_or_none(item.get("title"))
        created_at = _str_or_none(item.get("created_at"))
        kind = item.get("kind")
        if (
            link_id is None
            or title is None
            or created_at is None
            or not isinstance(kind, str)
            or kind not in _TRACKING_KINDS
        ):
            continue
        out.append(
            PlanTrackingLink(
                id=link_id,
                kind=kind,  # type: ignore[arg-type]
                title=title,
                url=_str_or_empty(item.get("url")),
                status=_str_or_empty(item.get("status")),
                ref=_str_or_empty(item.get("ref")),
                notes=_str_or_empty(item.get("notes")),
                created_at=created_at,
            )
        )
    return out


def _loop_status(value: object, fallback: PlanRunStatus) -> LoopStatus:
    if isinstance(value, str) and value in _LOOP_STATUSES:
        return value  # type: ignore[return-value]
    return loop_status_for_run_status(fallback)


def _latest_run_status(artifact: PlanArtifactSummary) -> str | None:
    return artifact.runs[-1].status if artifact.runs else None




def _heading(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def _hash_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _profile(value: object, default: PlanningProfile) -> PlanningProfile:
    candidate = value if isinstance(value, str) else default
    if candidate in _PROFILES:
        return candidate  # type: ignore[return-value]
    return default


def _framework(value: object, default: PlanningFramework) -> PlanningFramework:
    candidate = value if isinstance(value, str) else default
    if candidate in ("bmad", "spec", "openspec", "custom"):
        return candidate  # type: ignore[return-value]
    return default


def _depth(value: object, default: PlanningDepth) -> PlanningDepth:
    candidate = value if isinstance(value, str) else default
    if candidate in ("minimal", "lightweight", "standard", "deep", "custom"):
        return candidate  # type: ignore[return-value]
    return default


def _depth_for_profile(profile: PlanningProfile) -> PlanningDepth:
    if profile in ("bugfix", "hotfix"):
        return "minimal"
    if profile in ("refactor", "migration"):
        return "standard"
    if profile == "full_app":
        return "deep"
    if profile == "custom":
        return "custom"
    return "lightweight"


def _framework_label(framework: str) -> str:
    if framework == "spec":
        return "Spec-kit"
    if framework == "openspec":
        return "OpenSpec"
    if framework == "custom":
        return "Custom"
    return "BMAD"


def _phase(value: object) -> Literal["conversing", "planned"]:
    return "conversing" if value == "conversing" else "planned"


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _str_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int_or_default(value: object, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = [
    "PlanArtifactConflict",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "PlanningService",
]
