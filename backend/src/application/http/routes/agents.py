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
    AgentSummary,
    DetachResponse,
    NewAgentRequest,
)
from src.domain.commands.agents import delete, detach, list_for_work, start
from src.domain.connections import ConnectionStore, ContextFetchError
from src.domain.models import Agent, Context
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager, WorktreeProvisionFailed
from src.domain.sharedfolders.ports import ShareProvisioner, SharedFolderStore
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.infrastructure.filesystem.reveal import open_in_file_browser
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


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
SupervisorDep = Annotated[AgentSupervisorService, Depends(get_supervisor)]
WorktreeDep = Annotated[WorktreeManager, Depends(get_worktree_manager)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ConnectionStoreDep = Annotated[ConnectionStore, Depends(get_connection_store)]
ShareStoreDep = Annotated[SharedFolderStore, Depends(get_sharestore)]
ShareProvisionerDep = Annotated[ShareProvisioner, Depends(get_share_provisioner)]


@router.get("/works/{work_slug}/agents", response_model=list[AgentSummary])
def list_agents_for_work_endpoint(
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
) -> DetachResponse:
    """Stop the supervisor's SDK process for this agent, flip its status
    to ``detached``, and shell out to the user's terminal with the
    matching CLI resume command. If the terminal can't be launched (no
    detected emulator on Linux, sandbox restrictions, etc.) the response
    still includes the command string so the FE can copy-to-clipboard."""
    try:
        result = await detach.execute(
            workstore,
            supervisor,
            worktree_manager,
            detach.DetachAgentRequest(agent_slug=agent_slug),
        )
    except detach.AgentNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except detach.AgentNotResumable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return DetachResponse(command=result.command, launched=result.launched)


@router.delete("/agents/{agent_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_endpoint(
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
def reveal_agent_endpoint(
    agent_slug: str, workstore: WorkStoreDep, settings: SettingsDep
) -> None:
    """Open the agent's worktree (or source folder, if no worktree was
    provisioned) in the OS file browser. Symmetric with the work-level
    reveal — this one targets the dir where the adapter's CLI actually
    runs, so the user can poke at the agent's working tree."""
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
        open_in_file_browser(str(target))
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"reveal failed: {exc}",
        ) from exc


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
