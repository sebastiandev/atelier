"""Agents REST router.

POST /api/works/{work_slug}/agents creates an agent row + provisions a
git worktree + starts it on the supervisor. The orchestration sits in
``domain/commands/agents/start.py``; the route stays thin: parse →
command → format.

Wire format: provider + model + free ``options`` dict. The provider's
Spec validates ``options``; unknown keys → 422.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import AgentSummary, NewAgentRequest
from src.domain.commands.agents import list_for_work, start as start_cmd
from src.domain.models import Agent
from src.domain.supervisor import AgentSupervisorService
from src.domain.worktrees import WorktreeManager
from src.domain.workstore.ports import WorkStore
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


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
SupervisorDep = Annotated[AgentSupervisorService, Depends(get_supervisor)]
WorktreeDep = Annotated[WorktreeManager, Depends(get_worktree_manager)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


@router.get("/works/{work_slug}/agents", response_model=list[AgentSummary])
def list_agents_for_work_endpoint(
    work_slug: str, workstore: WorkStoreDep
) -> list[AgentSummary]:
    try:
        agents = list_for_work.execute(workstore, work_slug)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return [_to_summary(work_slug, a) for a in agents]


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
    settings: SettingsDep,
) -> AgentSummary:
    req = start_cmd.StartAgentRequest(
        work_slug=work_slug,
        name=payload.name,
        persona=payload.persona,
        role=payload.role,
        provider=payload.provider,
        model=payload.model,
        options=payload.options,
    )
    try:
        plan = start_cmd.execute(workstore, worktree_manager, settings, req)
    except start_cmd.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (start_cmd.InvalidProviderConfig, start_cmd.WorkFolderMissing) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    assert plan.agent.slug is not None
    await supervisor.start_agent(work_slug, plan.agent.slug, plan.adapter, plan.context)
    return _to_summary(work_slug, plan.agent)


def _to_summary(work_slug: str, agent: Agent) -> AgentSummary:
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
        status=agent.status,
        started_at=agent.started_at,
        stopped_at=agent.stopped_at,
    )
