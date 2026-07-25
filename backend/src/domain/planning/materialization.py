"""Planning materialization actions.

These actions hold framework-generated plan invariants shared by HTTP commands
and background orchestration. They are intentionally command-free so use cases
can compose the behavior without invoking another command.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from src.domain.agents import SPECS, CommonAgentConfig
from src.domain.chatstore.dtos import ChatGrounding, ChatRecord, CreateChatRequest
from src.domain.chatstore.ports import ChatStore
from src.domain.models import Provider
from src.domain.planning.dtos import (
    PlanArtifactEntry,
    PlanArtifactKind,
    PlanningFramework,
    PlanningFrameworkStatus,
    PlanningProfile,
    WorkPlanView,
)
from src.domain.planning.frameworks import (
    artifact_root_rel_path,
    check_framework_status,
    depth_for_profile,
)
from src.domain.planning.paths import atelier_planning_rel_path
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.prompts import PlanningMaterializationPrompt
from src.domain.planning.service import PlanningNotStarted, PlanningService
from src.domain.prompts import build_prompt
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

_MATERIALIZER_TITLE = "Planning materializer"
_REPORT_RE = re.compile(
    r'^\s*(\{\s*"atelier_plan_materialization"\s*:.*\})\s*$',
    re.MULTILINE,
)


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class PlanningFrameworkNotReady(ValueError):
    """The selected framework is not initialized in the working folder."""


class InvalidPlanMaterialization(ValueError):
    """The submitted materialization package is invalid."""


class ChatNotFound(ValueError):
    """The materialization chat doesn't exist."""


class InvalidMaterializationChat(ValueError):
    """The selected chat is not a materialization chat for this Work."""


class MaterializationReportNotFound(ValueError):
    """The materializer has not emitted a final report yet."""


class InvalidMaterializationReport(ValueError):
    """The materializer report is malformed."""


def submit_plan_materialization(
    workstore: WorkStore,
    files: PlanningFiles,
    *,
    work_slug: str,
    root_path: str,
    framework: PlanningFramework,
    profile: PlanningProfile,
    artifacts: tuple[PlanArtifactEntry, ...],
    artifact_root_path: str | None = None,
) -> WorkPlanView:
    """Persist metadata for framework-generated planning files.

    Preconditions: Work exists, framework is ready in ``root_path``, and every
    artifact path exists under the selected framework output folder.
    Postconditions: no Markdown source content is changed; manifest metadata
    and source hashes are updated.
    """
    record = workstore.get_work(work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    stored_work_slug = record.work.slug
    if not stored_work_slug:
        raise ValueError("work must have a slug")

    status = check_framework_status(framework, root_path)
    if not status.ready:
        command = " ".join(status.setup_command) or "initialize the selected framework"
        raise PlanningFrameworkNotReady(
            f"{status.label} is not initialized in {status.root_path}. "
            f"{status.setup_hint} Command: {command}"
        )

    bound_root_path = files.bind_working_root(stored_work_slug, root_path)
    files.ensure_plan_dir(stored_work_slug)
    artifact_root, absolute_artifact_root = resolve_artifact_root(
        bound_root_path, framework, stored_work_slug, artifact_root_path
    )
    _validate_entries(files, absolute_artifact_root, artifacts)
    now = _now_iso()
    depth = depth_for_profile(profile)
    manifest = files.read_manifest(stored_work_slug) or _new_manifest(
        stored_work_slug, framework, profile, depth, bound_root_path, now
    )
    manifest["framework"] = framework
    manifest["profile"] = profile
    manifest["depth"] = depth
    manifest["phase"] = "planned"
    manifest["root_path"] = bound_root_path
    manifest["artifact_root"] = artifact_root
    manifest["artifact_root_path"] = absolute_artifact_root
    manifest["updated_at"] = now
    manifest["artifacts"] = [_entry_to_manifest(entry) for entry in artifacts]
    files.write_manifest(stored_work_slug, manifest)

    index = PlanningService(files)
    plan = index.get_plan(stored_work_slug)
    if plan is None:
        raise PlanningNotStarted(f"planning not started: {stored_work_slug}")
    manifest["source_hashes"] = {row.path: row.source_hash for row in plan.artifacts}
    files.write_manifest(stored_work_slug, manifest)
    plan = index.get_plan(stored_work_slug)
    if plan is None:
        raise PlanningNotStarted(f"planning not started: {stored_work_slug}")
    return plan


def start_materialization_chat(
    workstore: WorkStore,
    chatstore: ChatStore,
    *,
    work_slug: str,
    root_path: str,
    framework: PlanningFramework,
    profile: PlanningProfile,
    provider: Provider,
    model: str,
    options: dict[str, Any],
    planning_chat_slug: str | None,
    artifact_root_path: str | None = None,
) -> tuple[ChatRecord, PlanningFrameworkStatus]:
    """Create or return a Planning materialization chat.

    Preconditions: Work exists, selected framework is initialized, and the
    provider config can write inside ``root_path``.
    Postconditions: a work-grounded materialization chat exists in
    ``root_path`` with backend-built instructions.
    """
    record = workstore.get_work(work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    status = check_framework_status(framework, root_path)
    if not status.ready:
        command = " ".join(status.setup_command) or "initialize the selected framework"
        raise PlanningFrameworkNotReady(
            f"{status.label} is not initialized in {status.root_path}. "
            f"{status.setup_hint} Command: {command}"
        )

    existing = _existing_materialization_chat(
        chatstore, work_slug, status.root_path, provider, model
    )
    if existing is not None:
        if existing.chat.slug:
            for key, value in materialization_options(provider, options).items():
                chatstore.set_chat_option(existing.chat.slug, key, value)
            refreshed = chatstore.get_chat(existing.chat.slug)
            if refreshed is not None:
                existing = refreshed
        return existing, status

    planning_chat = _planning_chat(chatstore, work_slug, planning_chat_slug)
    artifact_root, _absolute_artifact_root_path = resolve_artifact_root(
        status.root_path, framework, work_slug, artifact_root_path
    )
    first_message = build_prompt(
        PlanningMaterializationPrompt(
            work_slug=work_slug,
            work_name=record.work.name,
            root_path=status.root_path,
            atelier_planning_path=atelier_planning_rel_path(work_slug),
            artifact_root=artifact_root,
            framework=framework,
            profile=profile,
            planning_chat_slug=(
                planning_chat.chat.slug if planning_chat and planning_chat.chat.slug else None
            ),
            planning_chat_excerpt=_transcript_excerpt(planning_chat),
        )
    )
    return (
        chatstore.create_chat(
            CreateChatRequest(
                provider=provider,
                model=model,
                first_message=first_message,
                title=_MATERIALIZER_TITLE,
                grounding=ChatGrounding(kind="work", ref=work_slug),
                working_directory=status.root_path,
                options=materialization_options(provider, options),
            )
        ),
        status,
    )


def validate_materialization_provider_config(
    *,
    root_path: str,
    provider: Provider,
    model: str,
    options: dict[str, Any],
) -> None:
    """Validate provider/model/options for a materialization run.

    Preconditions: ``root_path`` is the intended working folder.
    Postconditions: raises if the provider cannot build a materializer config.
    """
    workdir = Path(root_path).expanduser()
    SPECS[provider].build(
        CommonAgentConfig(
            workdir=workdir,
            writable_roots=(workdir,),
            system_prompt="",
        ),
        model,
        materialization_options(provider, options),
    )


async def reset_materializer_runtime(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    chat_slug: str,
) -> None:
    """Reset one materializer to a fresh provider session.

    Preconditions: ``chat_slug`` identifies a stored materializer chat.
    Postconditions: its runtime is stopped and its generated files and transcript remain.
    """
    await supervisor.stop_agent(chat_slug)
    chatstore.clear_chat_session_id(chat_slug)


def finalize_materialization_report(
    workstore: WorkStore,
    chatstore: ChatStore,
    files: PlanningFiles,
    *,
    work_slug: str,
    chat_slug: str,
    framework: PlanningFramework,
    profile: PlanningProfile,
    artifact_root_path: str | None = None,
) -> WorkPlanView:
    """Finalize the latest materializer report for a Work.

    Preconditions: Work exists and every reported artifact exists on disk.
    Postconditions: the plan manifest and source hashes match the reported
    metadata and current files.
    """
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    record = chatstore.get_chat(chat_slug)
    if record is None:
        raise ChatNotFound(f"chat not found: {chat_slug}")
    chat = record.chat
    if (
        chat.title.strip().casefold() != _MATERIALIZER_TITLE.casefold()
        or chat.grounding_kind != "work"
        or chat.grounding_ref != work_slug
    ):
        raise InvalidMaterializationChat(
            f"chat {chat_slug} is not a materializer for {work_slug}"
        )
    if not chat.working_directory:
        raise InvalidMaterializationChat(f"chat {chat_slug} has no working folder")
    report = _latest_report(record.transcript)
    return submit_plan_materialization(
        workstore,
        files,
        work_slug=work_slug,
        root_path=chat.working_directory,
        framework=framework,
        profile=profile,
        artifacts=_entries_from_report(report),
        artifact_root_path=artifact_root_path,
    )


def materialization_options(provider: Provider, options: dict[str, Any]) -> dict[str, Any]:
    """Return provider options for a write-capable materializer run."""
    next_options = dict(options)
    if provider == "codex":
        next_options["sandbox"] = "workspace-write"
        next_options["approval_mode"] = "on-request"
    elif provider == "codex-acp":
        next_options["mode"] = "agent"
    elif provider == "claude-code":
        next_options["permission_mode"] = "acceptEdits"
    elif provider == "claude-acp":
        next_options["permission_mode"] = "acceptEdits"
    elif provider == "opencode":
        next_options["mode"] = "build"
    elif provider == "amp":
        next_options["permission_mode"] = "default"
    return next_options


def _validate_entries(
    files: PlanningFiles, artifact_root_path: str, entries: tuple[PlanArtifactEntry, ...]
) -> None:
    if not entries:
        raise InvalidPlanMaterialization("at least one artifact is required")
    paths: set[str] = set()
    for entry in entries:
        if not _valid_artifact_path(entry.path):
            raise InvalidPlanMaterialization(f"invalid artifact path: {entry.path}")
        if entry.path in paths:
            raise InvalidPlanMaterialization(f"duplicate artifact path: {entry.path}")
        if not entry.title.strip():
            raise InvalidPlanMaterialization(f"artifact title is required: {entry.path}")
        if files.read_text_at(artifact_root_path, entry.path) is None:
            raise InvalidPlanMaterialization(f"artifact file is missing: {entry.path}")
        paths.add(entry.path)
    for entry in entries:
        for dependency in entry.dependencies:
            if dependency not in paths:
                raise InvalidPlanMaterialization(
                    f"dependency {dependency!r} is not a submitted artifact path"
                )


def _valid_artifact_path(path: str) -> bool:
    if "\x00" in path or "\\" in path or not path.endswith(".md"):
        return False
    rel = PurePosixPath(path)
    return not rel.is_absolute() and all(part not in {"", ".", ".."} for part in rel.parts)


def _new_manifest(
    work_slug: str,
    framework: PlanningFramework,
    profile: PlanningProfile,
    depth: str,
    root_path: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "work_slug": work_slug,
        "framework": framework,
        "profile": profile,
        "phase": "planned",
        "depth": depth,
        "root_path": root_path,
        "artifact_root": "",
        "artifact_root_path": "",
        "created_at": created_at,
        "updated_at": created_at,
        "source_hashes": {},
        "artifacts": [],
        "approved_at": None,
        "approved_source_hashes": {},
        "accepted_artifacts": {},
        "artifact_runs": {},
        "artifact_proposals": {},
        "artifact_tracking": {},
        "edit_records": [],
    }


def resolve_artifact_root(
    root_path: str,
    framework: PlanningFramework,
    work_slug: str,
    artifact_root_path: str | None = None,
) -> tuple[str, str]:
    """Resolve the framework artifact root from a default or user override.

    Preconditions: ``root_path`` is the selected work folder and
    ``artifact_root_path`` is either empty, root-relative, or absolute inside
    ``root_path``.
    Postconditions: returns ``(relative_posix_path, absolute_path)`` and raises
    if the folder escapes ``root_path``.
    """
    if not artifact_root_path or not artifact_root_path.strip():
        rel = artifact_root_rel_path(framework, work_slug)
        return rel, _absolute_artifact_root(root_path, rel)

    root = Path(root_path).expanduser().resolve()
    raw = artifact_root_path.strip()
    raw_path = Path(raw).expanduser()
    if raw_path.is_absolute():
        absolute = raw_path.resolve()
    else:
        relative = PurePosixPath(raw)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise InvalidPlanMaterialization(
                f"invalid framework output folder: {artifact_root_path}"
            )
        absolute = root.joinpath(*relative.parts).resolve()
    try:
        rel_path = absolute.relative_to(root)
    except ValueError as exc:
        raise InvalidPlanMaterialization(
            f"framework output folder must be inside {root}"
        ) from exc
    if not rel_path.parts:
        raise InvalidPlanMaterialization(
            f"invalid framework output folder: {artifact_root_path}"
        )
    rel_posix = PurePosixPath(*rel_path.parts).as_posix()
    return rel_posix, str(absolute)


def _absolute_artifact_root(root_path: str, artifact_root: str) -> str:
    root = Path(root_path).expanduser().resolve()
    rel = PurePosixPath(artifact_root)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise InvalidPlanMaterialization(
            f"invalid framework output folder: {artifact_root}"
        )
    return str(root.joinpath(*rel.parts))


def _entry_to_manifest(entry: PlanArtifactEntry) -> dict[str, Any]:
    return {
        "path": entry.path,
        "title": entry.title.strip(),
        "artifact_kind": entry.artifact_kind,
        "executable": entry.executable,
        "dependencies": list(entry.dependencies),
    }


def _latest_report(transcript: list[Any]) -> dict[str, Any]:
    for message in reversed(transcript):
        if getattr(message, "role", None) != "assistant":
            continue
        body = getattr(message, "body", "")
        if not isinstance(body, str):
            continue
        matches = list(_REPORT_RE.finditer(body))
        if not matches:
            continue
        raw = matches[-1].group(1)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidMaterializationReport(
                "materialization report is not valid JSON"
            ) from exc
        body = parsed.get("atelier_plan_materialization")
        if not isinstance(body, dict):
            raise InvalidMaterializationReport(
                "materialization report body must be an object"
            )
        return body
    raise MaterializationReportNotFound(
        "no atelier_plan_materialization report found yet"
    )


def _entries_from_report(report: dict[str, Any]) -> tuple[PlanArtifactEntry, ...]:
    raw = report.get("artifacts")
    if not isinstance(raw, list):
        raise InvalidMaterializationReport("'artifacts' must be a list")
    entries: list[PlanArtifactEntry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise InvalidMaterializationReport(f"artifact {index} must be an object")
        entries.append(
            PlanArtifactEntry(
                path=_required_str(item, "path", index),
                title=_required_str(item, "title", index),
                artifact_kind=_artifact_kind(item.get("artifact_kind"), index),
                executable=_required_bool(item, "executable", index),
                dependencies=tuple(_dependencies(item.get("dependencies"), index)),
            )
        )
    return tuple(entries)


def _required_str(item: dict[str, Any], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidMaterializationReport(
            f"artifact {index} has missing or empty {key!r}"
        )
    return value


def _required_bool(item: dict[str, Any], key: str, index: int) -> bool:
    value = item.get(key)
    if not isinstance(value, bool):
        raise InvalidMaterializationReport(
            f"artifact {index} field {key!r} must be a boolean"
        )
    return value


def _artifact_kind(value: object, index: int) -> PlanArtifactKind:
    allowed = {
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
    if value not in allowed:
        raise InvalidMaterializationReport(
            f"artifact {index} has unknown artifact_kind {value!r}"
        )
    return value  # type: ignore[return-value]


def _dependencies(value: object, index: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidMaterializationReport(
            f"artifact {index} field 'dependencies' must be a string list"
        )
    return list(value)


def _existing_materialization_chat(
    chatstore: ChatStore,
    work_slug: str,
    root_path: str,
    provider: Provider,
    model: str,
) -> ChatRecord | None:
    normalized_root = str(Path(root_path).expanduser())
    for record in chatstore.list_chats():
        chat = record.chat
        if (
            chat.title.strip().casefold() == _MATERIALIZER_TITLE.casefold()
            and chat.grounding_kind == "work"
            and chat.grounding_ref == work_slug
            and str(Path(chat.working_directory or "").expanduser()) == normalized_root
            and chat.provider == provider
            and chat.model == model
        ):
            return record
    return None


def _planning_chat(
    chatstore: ChatStore, work_slug: str, chat_slug: str | None
) -> ChatRecord | None:
    if chat_slug:
        record = chatstore.get_chat(chat_slug)
        if record is not None:
            return record
    for record in chatstore.list_chats():
        chat = record.chat
        if (
            chat.title.strip().casefold() == "planning"
            and chat.grounding_kind == "work"
            and chat.grounding_ref == work_slug
        ):
            return record
    return None


def _transcript_excerpt(record: ChatRecord | None) -> str:
    if record is None:
        return ""
    lines: list[str] = []
    for message in record.transcript[-12:]:
        body = " ".join(message.body.split())
        if len(body) > 1200:
            body = body[:1197].rstrip() + "..."
        lines.append(f"{message.role}: {body}")
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "ChatNotFound",
    "InvalidMaterializationChat",
    "InvalidMaterializationReport",
    "InvalidPlanMaterialization",
    "MaterializationReportNotFound",
    "PlanningFrameworkNotReady",
    "WorkNotFound",
    "finalize_materialization_report",
    "materialization_options",
    "resolve_artifact_root",
    "start_materialization_chat",
    "submit_plan_materialization",
    "validate_materialization_provider_config",
]
