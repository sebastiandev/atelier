"""Agents REST router.

POST /api/works/{work_slug}/agents creates an agent row via the workstore
then starts it on the supervisor with a provider-specific adapter
selected via the SPECS registry + ``build_adapter`` singledispatch.

The wire format is: provider + model + free ``options`` dict. Each
provider's Spec validates the ``options`` contents; unknown keys are
rejected with 422.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import AgentSummary, NewAgentRequest
from src.domain.agents import (
    SPECS,
    AgentStartContext,
    CommonAgentConfig,
    render_system_prompt,
)
from src.domain.commands.agents import list_for_work
from src.domain.models import Agent
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.dtos import AddAgentRequest
from src.domain.workstore.ports import WorkStore
from src.infrastructure.agents import build_adapter
from src.settings import Settings

router = APIRouter()


def get_workstore(request: Request) -> WorkStore:
    return request.app.state.workstore  # type: ignore[no-any-return]


def get_supervisor(request: Request) -> AgentSupervisorService:
    return request.app.state.supervisor  # type: ignore[no-any-return]


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
SupervisorDep = Annotated[AgentSupervisorService, Depends(get_supervisor)]
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
    settings: SettingsDep,
) -> AgentSummary:
    try:
        agent = workstore.add_agent_to_work(
            AddAgentRequest(
                work_slug=work_slug,
                name=payload.name,
                persona=payload.persona,
                role=payload.role,
                provider=payload.provider,
                model=payload.model,
            )
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    if agent.slug is None:  # defensive; repo guarantees this
        raise RuntimeError("workstore returned agent without slug")

    workdir = Path(f"/tmp/atelier/{work_slug}/{agent.slug}")
    workdir.mkdir(parents=True, exist_ok=True)
    common = CommonAgentConfig(
        workdir=workdir,
        system_prompt=render_system_prompt(payload.persona, payload.role),
    )
    try:
        config = SPECS[payload.provider].build(common, payload.model, payload.options)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    adapter = build_adapter(config, settings)
    context = AgentStartContext(
        workdir=common.workdir,
        context_md=common.context_md,
        model=payload.model,
        system_prompt=common.system_prompt,
    )
    await supervisor.start_agent(work_slug, agent.slug, adapter, context)

    return _to_summary(work_slug, agent)


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
