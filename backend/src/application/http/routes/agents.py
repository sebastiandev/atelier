"""Agents REST router.

POST /api/works/{work_slug}/agents creates an agent row + provisions a
git worktree + starts it on the supervisor. The orchestration sits in
``domain/commands/agents/start.py`` — async command that drives the
supervisor directly. The route stays thin: parse → call command →
format.

Wire format: provider + model + free ``options`` dict + folder. The
provider's Spec validates ``options``; unknown keys → 422.
"""

import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    AgentCompactionSummaryResponse,
    AgentSummary,
    CompactAgentRequest,
    CompactAgentResponse,
    DetachResponse,
    NewAgentRequest,
    PatchAgentRequest,
    SwitchThreadRequest,
)
from src.domain.agents.compactions import CompactionSessionClient
from src.domain.agents.handoffs import Summarizer
from src.domain.commands.agents import (
    compact,
    delete,
    detach,
    list_for_work,
    read_compaction_summary,
    rename,
    start,
    switch_thread,
)
from src.domain.connections import ConnectionStore, ContextFetchError
from src.domain.models import Agent, Context
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager, WorktreeProvisionFailed
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.infrastructure.filesystem.reveal import open_in_file_browser
from src.infrastructure.filesystem.terminal import open_in_terminal
from src.settings import Settings

router = APIRouter()


def get_workstore(request: Request) -> WorkStore:
    return request.app.state.workstore  # type: ignore[no-any-return]


def get_supervisor(request: Request) -> AgentSupervisorService:
    return request.app.state.supervisor  # type: ignore[no-any-return]


def get_worktree_manager(request: Request) -> WorktreeManager:
    return request.app.state.worktree_manager  # type: ignore[no-any-return]


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_connection_store(request: Request) -> ConnectionStore:
    return request.app.state.connection_store  # type: ignore[no-any-return]


def get_sharestore(request: Request) -> SharedFolderStore:
    return request.app.state.sharestore  # type: ignore[no-any-return]


def get_share_provisioner(request: Request) -> ShareProvisioner:
    return request.app.state.share_provisioner  # type: ignore[no-any-return]


def get_summarizer(request: Request) -> Summarizer:
    return request.app.state.summarizer  # type: ignore[no-any-return]


def get_compaction_session_client(request: Request) -> CompactionSessionClient:
    return request.app.state.compaction_session_client  # type: ignore[no-any-return]


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
SupervisorDep = Annotated[AgentSupervisorService, Depends(get_supervisor)]
WorktreeDep = Annotated[WorktreeManager, Depends(get_worktree_manager)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ConnectionStoreDep = Annotated[ConnectionStore, Depends(get_connection_store)]
ShareStoreDep = Annotated[SharedFolderStore, Depends(get_sharestore)]
ShareProvisionerDep = Annotated[ShareProvisioner, Depends(get_share_provisioner)]
SummarizerDep = Annotated[Summarizer, Depends(get_summarizer)]
CompactionSessionClientDep = Annotated[
    CompactionSessionClient, Depends(get_compaction_session_client)
]


@router.get("/works/{work_slug}/agents", response_model=list[AgentSummary])
def list_agents_for_work(
    work_slug: str, workstore: WorkStoreDep, settings: SettingsDep
) -> list[AgentSummary]:
    try:
        agents = list_for_work.execute(workstore, work_slug)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    return [_to_summary(work_slug, a, paths) for a in agents]


@router.post(
    "/works/{work_slug}/agents",
    response_model=AgentSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    work_slug: str,
    payload: NewAgentRequest,
    workstore: WorkStoreDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    settings: SettingsDep,
) -> AgentSummary:
    req = start.StartAgentRequest(
        work_slug=work_slug,
        name=payload.name,
        persona=payload.persona,
        role=payload.role,
        provider=payload.provider,
        model=payload.model,
        # Expand ~ here so the persisted folder is canonical — the rest
        # of the stack (worktree manager, adapter cwd, mkdir-on-start)
        # sees a real absolute path instead of a tilde-relative string.
        folder=Path(payload.folder).expanduser(),
        options=payload.options,
        contexts=tuple(
            Context(type=c.type, value=c.value, conn_id=c.conn_id)
            for c in payload.contexts
        ),
        fork_from_agent=payload.fork_from_agent,
        branch_name=payload.branch_name,
    )
    try:
        agent = await start.execute(
            workstore,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            settings,
            req,
        )
    except start.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (start.InvalidProviderConfig, start.AgentFolderMissing) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except ContextFetchError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except WorktreeProvisionFailed as e:
        # The agent row + workspace dir have already been rolled back
        # by start.execute. Surface stderr so the FE toast tells the
        # user *why* (e.g. "branch already exists / checked out at
        # missing path"), not just "non-zero exit".
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Couldn't provision the agent's worktree: {e.stderr or e!s}",
        ) from e

    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    return _to_summary(work_slug, agent, paths)


@router.post("/agents/{agent_slug}/detach", response_model=DetachResponse)
async def detach_agent(
    agent_slug: str,
    workstore: WorkStoreDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    kind: str = "system",
) -> DetachResponse:
    """Stop the supervisor's SDK process for this agent, flip its status
    to ``detached``, and shell out to the user's terminal with the
    matching CLI resume command. If the terminal can't be launched (no
    detected emulator on Linux, sandbox restrictions, etc.) the response
    still includes the command string so the FE can copy-to-clipboard.

    ``kind`` picks the terminal app — same values as the open-in-console
    endpoint above (``system`` / ``iterm2`` / ``terminator`` / ...).
    Unknown values fall back to ``system``."""
    try:
        result = await detach.execute(
            workstore,
            supervisor,
            worktree_manager,
            detach.DetachAgentRequest(agent_slug=agent_slug, terminal=kind),
            sharestore=sharestore,
            share_provisioner=share_provisioner,
        )
    except detach.AgentNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except detach.AgentNotResumable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return DetachResponse(command=result.command, launched=result.launched)


@router.patch("/agents/{agent_slug}", response_model=AgentSummary)
def patch_agent(
    agent_slug: str,
    payload: PatchAgentRequest,
    workstore: WorkStoreDep,
    settings: SettingsDep,
) -> AgentSummary:
    """Update mutable fields on an agent. Today only ``name`` is mutable;
    every other field on the agent shape is FS-canonical and set once at
    create time."""
    work_slug = workstore.get_work_slug_for_agent(agent_slug)
    if work_slug is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"agent not found: {agent_slug}"
        )
    agent: Agent | None
    if payload.name is not None:
        try:
            agent = rename.execute(
                workstore,
                rename.RenameAgentRequest(agent_slug=agent_slug, name=payload.name),
            )
        except rename.AgentNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    else:
        agent = next(
            (a for a in workstore.list_agents_for_work(work_slug) if a.slug == agent_slug),
            None,
        )
    if agent is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"agent not found: {agent_slug}"
        )
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    return _to_summary(work_slug, agent, paths)


@router.delete("/agents/{agent_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_slug: str,
    workstore: WorkStoreDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
) -> None:
    """Permanently remove an agent: stop the supervisor task, remove the
    per-agent git worktree, and wipe the workspace dir + DB row. The
    parent work and its other agents are untouched."""
    try:
        await delete.execute(
            workstore,
            supervisor,
            worktree_manager,
            delete.DeleteAgentRequest(agent_slug=agent_slug),
        )
    except delete.AgentNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/agents/{agent_slug}/reveal", status_code=status.HTTP_204_NO_CONTENT)
def reveal_agent(
    agent_slug: str,
    workstore: WorkStoreDep,
    settings: SettingsDep,
    kind: str = "worktree",
) -> None:
    """Open one of the agent's filesystem locations in the OS file browser.

    ``kind`` picks which dir to reveal:
      - ``worktree`` (default) — the per-agent git worktree (or source
        folder fallback) where the SDK process runs. Most-useful for
        poking at code the agent produced.
      - ``atelier`` — Atelier's per-agent bookkeeping dir under
        ``~/Atelier/works/<work>/agents/<agent>/`` (transcript.ndjson,
        agent.json, contexts/). Useful for inspecting the canonical
        Atelier state.

    Unknown values fall back to ``worktree`` to preserve the legacy
    no-arg call shape."""
    work_slug = workstore.get_work_slug_for_agent(agent_slug)
    if work_slug is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"agent not found: {agent_slug}"
        )
    agent = next(
        (a for a in workstore.list_agents_for_work(work_slug) if a.slug == agent_slug),
        None,
    )
    if agent is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"agent not found: {agent_slug}"
        )
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    if kind == "atelier":
        target = paths.agent_dir(work_slug, agent_slug)
    else:
        target = _resolve_worktree_path(paths, work_slug, agent_slug, agent.folder)
    try:
        open_in_file_browser(str(target))
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"reveal failed: {exc}",
        ) from exc


@router.post(
    "/agents/{agent_slug}/open-in-console",
    status_code=status.HTTP_204_NO_CONTENT,
)
def open_agent_in_console(
    agent_slug: str,
    workstore: WorkStoreDep,
    settings: SettingsDep,
    kind: str = "system",
) -> None:
    """Open the user's terminal with CWD set to the agent's worktree
    (or source folder, if no worktree was provisioned). Mirrors the
    reveal endpoint above but launches a terminal instead of the file
    browser. The ``kind`` query param picks a specific terminal app
    (``system`` / ``iterm2`` / ``terminator`` / ``gnome-terminal`` /
    ``konsole`` / ``tmux``); unknown values fall back to ``system``."""
    work_slug = workstore.get_work_slug_for_agent(agent_slug)
    if work_slug is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"agent not found: {agent_slug}"
        )
    agent = next(
        (a for a in workstore.list_agents_for_work(work_slug) if a.slug == agent_slug),
        None,
    )
    if agent is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"agent not found: {agent_slug}"
        )
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    target = _resolve_worktree_path(paths, work_slug, agent_slug, agent.folder)
    try:
        open_in_terminal(str(target), kind=kind)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"open in console failed: {exc}",
        ) from exc


@router.post(
    "/agents/{agent_slug}/switch-thread",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def switch_agent_thread(
    agent_slug: str,
    payload: SwitchThreadRequest,
    workstore: WorkStoreDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    settings: SettingsDep,
) -> None:
    """Switch the agent's underlying provider thread to ``thread_id``.

    Stops the current adapter, persists the new ``session_id``, writes a
    ``handoff_accepted`` transcript marker, and re-registers the agent
    lazily so the next user input spawns a fresh CLI subprocess against
    the new thread.
    """
    try:
        await switch_thread.execute(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            switch_thread.SwitchThreadRequest(
                agent_slug=agent_slug,
                new_thread_id=payload.thread_id,
            ),
        )
    except switch_thread.AgentNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except switch_thread.InvalidThreadId as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/agents/{agent_slug}/compact", response_model=CompactAgentResponse)
async def compact_agent(
    agent_slug: str,
    payload: CompactAgentRequest,
    workstore: WorkStoreDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    settings: SettingsDep,
    summarizer: SummarizerDep,
    session_client: CompactionSessionClientDep,
) -> CompactAgentResponse:
    try:
        result = await compact.execute(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            summarizer,
            session_client,
            compact.CompactAgentRequest(
                agent_slug=agent_slug,
                reason=payload.reason,
            ),
        )
    except compact.AgentNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except compact.AgentNotCompactable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except compact.AgentBusy as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return CompactAgentResponse(
        agent_slug=result.agent_slug,
        work_slug=result.work_slug,
        provider=result.provider,
        old_session_id=result.old_session_id,
        new_session_id=result.new_session_id,
        summary_path=result.summary_path,
        breadcrumb_written=result.breadcrumb_written,
        breadcrumb_error=result.breadcrumb_error,
    )


@router.get(
    "/agents/{agent_slug}/compactions/{filename}",
    response_model=AgentCompactionSummaryResponse,
)
def get_agent_compaction_summary(
    agent_slug: str, filename: str, workstore: WorkStoreDep
) -> AgentCompactionSummaryResponse:
    try:
        result = read_compaction_summary.execute(
            workstore,
            read_compaction_summary.ReadCompactionSummaryRequest(
                agent_slug=agent_slug,
                filename=filename,
            ),
        )
    except read_compaction_summary.AgentNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except read_compaction_summary.CompactionSummaryNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return AgentCompactionSummaryResponse(
        agent_slug=result.agent_slug,
        work_slug=result.work_slug,
        filename=result.filename,
        summary_path=result.summary_path,
        content=result.content,
    )


def _to_summary(work_slug: str, agent: Agent, paths: WorkspacePaths) -> AgentSummary:
    if agent.slug is None:
        raise RuntimeError("persisted agent missing slug")
    return AgentSummary(
        slug=agent.slug,
        work_slug=work_slug,
        name=agent.name,
        persona=agent.persona,
        role=agent.role,
        provider=agent.provider,
        model=agent.model,
        folder=str(agent.folder),
        status=agent.status,
        started_at=agent.started_at,
        stopped_at=agent.stopped_at,
        worktree_path=str(_resolve_worktree_path(paths, work_slug, agent.slug, agent.folder)),
    )


def _resolve_worktree_path(
    paths: WorkspacePaths, work_slug: str, agent_slug: str, folder: Path
) -> Path:
    """The directory the adapter actually runs in. Mirrors
    ``WorktreeManager.ensure``'s contract: the per-agent worktree if it
    was provisioned (git source folder), otherwise the source folder."""
    candidate = paths.worktree_dir(work_slug, agent_slug)
    if candidate.exists():
        return candidate
    return folder
