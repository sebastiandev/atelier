"""Prompt builders for source-backed Planning chats."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from shlex import join as shell_join
from typing import get_args

from src.domain.planning.dtos import (
    PlanArtifactKind,
    PlanningFramework,
    PlanningProfile,
)
from src.domain.planning.frameworks import depth_for_profile, framework_definition
from src.domain.prompts import build_prompt

_READY_MARKER_EXAMPLE = (
    '{"atelier_planning_ready":{"ready":true,"summary":"one sentence"}}'
)


@dataclass(frozen=True)
class PlanningChatInitialPrompt:
    """Inputs for the first user-visible Planning chat message."""

    work_slug: str
    work_name: str
    project_name: str | None
    idea: str
    framework: PlanningFramework
    profile: PlanningProfile


@dataclass(frozen=True)
class PlanningDocumentRef:
    """One source-backed planning document available to the Planning chat."""

    path: str
    title: str
    artifact_kind: str
    executable: bool


@dataclass(frozen=True)
class PlanningChatRuntimePrompt:
    """Inputs needed to render a Planning chat runtime prompt."""

    chat_slug: str
    phase: str
    framework: PlanningFramework
    profile: PlanningProfile
    workdir: Path
    working_label: str
    working_details: str
    link_label: str
    link_details: str
    source_path: str | None = None
    documents: tuple[PlanningDocumentRef, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlanningChatTurnContextPrompt:
    """Hidden per-turn Planning context prepended before a visible user input."""

    source_path: str
    documents: tuple[PlanningDocumentRef, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlanningFrameworkSetupPrompt:
    """Inputs for a framework setup chat's first message."""

    work_slug: str
    work_name: str
    framework: PlanningFramework
    profile: PlanningProfile
    root_path: str


@dataclass(frozen=True)
class PlanningMaterializationPrompt:
    """Inputs for a write-capable materialization run prompt."""

    work_slug: str
    work_name: str
    root_path: str
    atelier_planning_path: str
    plan_artifacts_dir: str
    framework: PlanningFramework
    profile: PlanningProfile
    planning_chat_slug: str | None
    planning_chat_excerpt: str


@dataclass(frozen=True)
class PlanningMaterializerRuntimePrompt:
    """Inputs for a materializer provider's persistent system prompt."""

    framework: PlanningFramework
    root_path: str
    plan_artifacts_dir: str


@dataclass(frozen=True)
class PlanningMaterializationRecoveryPrompt:
    """Inputs for resuming materialization in a fresh provider session."""

    framework: PlanningFramework
    plan_artifacts_dir: str
    original_brief: str


@dataclass(frozen=True)
class PlanningMaterializationReportPrompt:
    """Inputs for requesting a missing report after materialization ends."""

    framework: PlanningFramework
    plan_artifacts_dir: str


@build_prompt.register
def _(req: PlanningChatInitialPrompt) -> str:
    """Render the first visible user message for a Planning chat."""
    fw = framework_definition(req.framework)
    profile = req.profile
    clean_idea = req.idea.strip() or req.work_name
    return (
        f"Start planning {req.work_slug} - {req.work_name} with {fw.label} "
        f"using the {profile} profile ({depth_for_profile(profile)} depth).\n\n"
        f"Goal: {clean_idea}\n\n"
        "Introduce how you will approach this planning session, state the "
        "initial assumptions you can make, and ask the most important focused "
        "questions needed to produce a reviewable source plan."
    )


@build_prompt.register
def _(req: PlanningMaterializationPrompt) -> str:
    """Render the backend-owned prompt for a Planning materializer run."""
    fw = framework_definition(req.framework)
    artifact_kinds = ", ".join(get_args(PlanArtifactKind))
    lines = [
        "Materialize an Atelier source-backed plan.",
        "",
        "You are a write-capable planning materializer, not the discovery chat.",
        "",
        "Work context:",
        f"- Work: {req.work_slug} - {req.work_name}",
        f"- Framework: {fw.label}",
        f"- Profile: {req.profile}",
        f"- Working folder: {req.root_path}",
        f"- Atelier state folder: {req.atelier_planning_path}",
        f"- Framework output folder: {req.plan_artifacts_dir}",
        "",
        f"{fw.label} is the authoritative framework for this run. Do not load, "
        "invoke, or adopt conventions from another planning framework.",
        "Folder and file names are paths only. In particular, the framework "
        "output folder does not identify or change the selected framework.",
        "Use the selected planning framework's local roles, skills, templates, "
        "and conventions when they are available in this repo.",
        "Do not force a fixed Atelier document set. Let the framework shape "
        "the files it needs, then classify each file into Atelier's known "
        "artifact kinds.",
        "",
        "Requirements:",
        "- Write every framework-generated planning source file under "
        f"`{req.plan_artifacts_dir}/`.",
        "- Do not write framework artifacts under the Atelier state folder; "
        "Atelier uses that folder for manifest and index metadata.",
        "- Generate the complete plan now: include all framework-level grouping "
        "documents and all executable stories, tasks, spikes, bugs, hotfixes, "
        "or follow-up work items that are currently known or implied.",
        "- Do not leave executable artifacts as later scoping placeholders. "
        "Each executable item should be specific enough for a user to review "
        "it and hand it to an implementation agent without first asking "
        "another agent to define the story/task.",
        "- If the framework normally creates work incrementally, override that "
        "behavior for Atelier materialization: create the full reviewable set "
        "now, with dependencies showing the intended order.",
        "- Do not make HTTP requests, browse the web, install packages, or call "
        "external services; materialization should use the selected repo, the "
        "Planning conversation, and installed framework assets only.",
        "- If a local framework command would require network access or package "
        "installation, skip it and write the planning files directly from the "
        "available local templates/context.",
        "- Keep paths in the final report relative to the framework output folder.",
        "- Do not include Markdown file content in the final report.",
        "- Use dependencies as relative artifact paths from the same final report.",
        "- Mark executable items such as implementation stories, tasks, spikes, "
        "bugs, or hotfixes with executable=true.",
        f"- artifact_kind must be one of: {artifact_kinds}.",
        "",
        "When the files exist on disk, finish with exactly one single-line JSON report:",
        '{"atelier_plan_materialization":{"artifacts":[{"path":"relative-file.md",'
        '"title":"Human title","artifact_kind":"story","executable":true,'
        '"dependencies":["dependency.md"]}]}}',
        "",
        "Discovery conversation context:",
    ]
    if req.planning_chat_slug:
        lines.append(f"- Planning chat: {req.planning_chat_slug}")
    lines.extend(
        [
            "",
            req.planning_chat_excerpt.strip()
            or "No Planning chat transcript was available.",
        ]
    )
    return "\n".join(lines)


@build_prompt.register
def _(req: PlanningMaterializerRuntimePrompt) -> str:
    """Render the persistent system prompt for a Planning materializer."""
    fw = framework_definition(req.framework)
    return (
        "You are Atelier's write-capable Planning materializer.\n"
        f"The selected framework is {fw.label}; it is authoritative for this "
        "entire run and every recovered provider session.\n"
        "Never infer or switch planning frameworks from a directory or file "
        "name. Planning folders and files are paths only.\n"
        f"Repository root: {req.root_path}\n"
        f"Planning output path: {req.plan_artifacts_dir}\n\n"
        "Follow the materialization brief supplied as user input. Preserve "
        "valid files already written by an earlier session. Inventory the "
        "planning output once, use targeted reads for concrete gaps, validate "
        "the finished set once, and emit the required structured report. Do "
        "not repeatedly audit the repository or reload unrelated framework "
        "skills. Keep every tool result below 12,000 characters: use indexes, "
        "headings, searches, and small file ranges instead of concatenating "
        "whole planning documents."
    )


@build_prompt.register
def _(req: PlanningMaterializationRecoveryPrompt) -> str:
    """Render a complete recovery brief for a fresh provider session."""
    fw = framework_definition(req.framework)
    return (
        "Resume an interrupted Atelier Planning materialization in this fresh "
        "provider session.\n"
        f"The selected framework is {fw.label}; it remains authoritative.\n"
        f"The output folder `{req.plan_artifacts_dir}` is only a path. Its name does "
        "not select or imply a framework.\n"
        "Treat files already present there as prior completed work. Inventory "
        "that folder once, preserve valid content, and inspect only the sources "
        "needed to close a specific remaining gap. Then perform one final "
        "consistency check and emit the required report. Do not restart broad "
        "discovery or repeat completed work.\n"
        "Do not read framework skill files, AGENTS guidance, or other setup "
        "instructions again; prior materialization sessions already loaded "
        "them. The original brief below supplies task context, and its framework "
        "asset instructions do not require another setup pass.\n"
        "Keep every tool result below 12,000 characters. Read the output README "
        "or index first; when it already enumerates a complete set and no "
        "concrete gap is known, use that index to build the report without "
        "concatenating full documents.\n\n"
        "<original_materialization_brief>\n"
        f"{req.original_brief.strip()}\n"
        "</original_materialization_brief>"
    )


@build_prompt.register
def _(req: PlanningMaterializationReportPrompt) -> str:
    """Render the bounded nudge used when a completed turn omitted its report."""
    fw = framework_definition(req.framework)
    return (
        "The materialization turn ended without its required "
        "atelier_plan_materialization report.\n"
        f"The selected framework remains {fw.label}. The output folder "
        f"`{req.plan_artifacts_dir}` is only a path and has no framework meaning.\n"
        "Do not restart planning, scan the repository, or load framework "
        "skills. Treat the planning files as finished. Inspect only the output "
        "folder as needed to enumerate its Markdown artifacts, then emit "
        "exactly one single-line atelier_plan_materialization JSON report with "
        "path, title, artifact_kind, executable, and dependencies for every "
        "artifact. Paths and dependencies must be relative to the output "
        "folder. Do not include Markdown content."
    )


@build_prompt.register
def _(req: PlanningChatRuntimePrompt) -> str:
    """Render the provider-facing prompt for a Planning chat runtime."""
    fw = framework_definition(req.framework)
    roles = "\n".join(
        f"- {role.name}: {role.description}" for role in fw.roles
    )
    phase_block = _phase_block(req)
    return (
        "You are Atelier's Planning chat for a Work.\n"
        f"Chat: {req.chat_slug} - Planning\n"
        "Purpose: orchestrate the selected planning framework before "
        "implementation agents are launched.\n\n"
        "Framework setup:\n"
        f"- Framework: {fw.label}\n"
        f"- Profile: {req.profile}\n"
        f"- Phase: {req.phase}\n"
        f"- Framework focus: {_focus_for_phase(req)}\n\n"
        "Available framework roles/lenses:\n"
        f"{roles}\n\n"
        f"{phase_block}\n\n"
        f"Working directory: {req.workdir}\n"
        f"Working folder: {req.working_label}\n"
        f"{req.working_details}\n\n"
        f"Linked to: {req.link_label}\n"
        f"{req.link_details}\n\n"
        "Start the first assistant turn with a brief user-facing introduction "
        "that names the goal, selected framework, and planning approach.\n"
        "Use the selected framework's roles/lenses when the user invokes them.\n"
        "Ask at most three focused clarifying questions when critical "
        "information is missing. If the idea is already clear enough, state "
        "assumptions and propose the first plan outline. Identify source "
        "areas to inspect, risks, dependencies, validation strategy, and "
        "first reasonable stories, specs, tasks, spikes, or bugs. Treat "
        "source-plan creation as a complete handoff target: propose the full "
        "set of framework-level docs and executable work items the user should "
        "be able to review, revise, and assign to agents."
    )


@build_prompt.register
def _(req: PlanningChatTurnContextPrompt) -> str:
    """Render hidden per-turn Planning context for provider input."""
    return (
        "<atelier_planning_context>\n"
        f"Source-backed planning folder: {req.source_path}\n"
        "The current Planning document index is:\n"
        f"{_document_index_lines(req.documents)}\n"
        "Use this refreshed index as implicit context for the user's message. "
        "The user does not need to mention a document explicitly for you to "
        "know these planning files exist. When editing or reading files, treat "
        "paths as relative to the source-backed planning folder unless the user "
        "provides an absolute path.\n"
        "</atelier_planning_context>"
    )


@build_prompt.register
def _(req: PlanningFrameworkSetupPrompt) -> str:
    """Render the backend-owned setup prompt for a Planning framework."""
    fw = framework_definition(req.framework)
    command = shell_join(fw.setup_command) if fw.setup_command else ""
    command_block = (
        f"- Run this setup command from the selected folder: `{command}`.\n"
        if command
        else "- No setup command is required for this framework.\n"
    )
    return (
        "Set up the selected Atelier planning framework in this repository.\n\n"
        "Work context:\n"
        f"- Work: {req.work_slug} - {req.work_name}\n"
        f"- Selected folder: {req.root_path}\n"
        f"- Framework: {fw.label}\n"
        f"- Profile: {req.profile}\n"
        f"- Depth: {depth_for_profile(req.profile)}\n\n"
        "Setup task:\n"
        f"{command_block}"
        "- Keep all changes scoped to framework initialization files and folders.\n"
        "- Do not create Atelier source-plan files or implementation changes.\n"
        "- After setup, report which framework markers now exist and whether "
        "Atelier can continue Planning."
    )


def _focus_for_phase(req: PlanningChatRuntimePrompt) -> str:
    fw = framework_definition(req.framework)
    return fw.revision_focus if req.phase == "revision" else fw.discovery_focus


def _phase_block(req: PlanningChatRuntimePrompt) -> str:
    if req.phase == "revision":
        source_path = req.source_path or "the source-backed planning folder"
        documents = _document_index_block(req.documents)
        return (
            "Revision phase:\n"
            f"- Source-backed planning files already exist at {source_path}.\n"
            f"{documents}"
            "- Users may reference plan documents with `@relative/path.md`; "
            "treat those mentions as references to files under the "
            "source-backed planning folder.\n"
            "- You may help the user revise those planning Markdown files.\n"
            "- Keep edits scoped to the source-backed planning folder unless Atelier launches "
            "a separate implementation agent.\n"
            "- When asked to change the plan, update the relevant planning "
            "documents and summarize what changed."
        )
    return (
        "Discovery phase:\n"
        "- No source-backed planning artifacts have been created yet.\n"
        "- You may inspect/read the selected working folder when useful.\n"
        "- Do not write files, create .atelier planning folders, launch agents, "
        "or claim artifacts exist.\n"
        "- When the plan is ready to materialize, end with a compact "
        "'Ready to create source plan' summary containing framework, profile, "
        "metadata-only source artifacts, and proposed executable items, then "
        "tell the user to create the source plan from Atelier. Then emit this "
        "exact single-line JSON marker on its own line:\n"
        f"{_READY_MARKER_EXAMPLE}\n"
        "Do not paste full document contents into the summary."
    )


def _document_index_block(documents: tuple[PlanningDocumentRef, ...]) -> str:
    if not documents:
        return ""
    lines = ["- Planning source documents available for `@` references:"]
    lines.extend(f"  - {line}" for line in _document_index_rows(documents))
    return "\n".join(lines) + "\n"


def _document_index_lines(documents: tuple[PlanningDocumentRef, ...]) -> str:
    if not documents:
        return "- No planning documents are currently indexed."
    return "\n".join(f"- {line}" for line in _document_index_rows(documents))


def _document_index_rows(documents: tuple[PlanningDocumentRef, ...]) -> list[str]:
    rows: list[str] = []
    for doc in documents:
        executable = "executable" if doc.executable else "source"
        rows.append(
            f"@{doc.path} — {doc.title} ({doc.artifact_kind}, {executable})"
        )
    return rows


__all__ = [
    "PlanningChatInitialPrompt",
    "PlanningChatRuntimePrompt",
    "PlanningChatTurnContextPrompt",
    "PlanningFrameworkSetupPrompt",
    "PlanningMaterializationPrompt",
]
