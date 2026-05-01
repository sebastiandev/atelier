"""Agents REST router (walking-skeleton scope).

POST /api/works/{work_slug}/agents creates an agent row via the workstore
and immediately starts it on the supervisor with a `StubAgentAdapter`.
STORY-011 will replace the stub with a real-provider registry; the
``provider`` field on the request is honoured in the persisted row but
ignored by the adapter selection until then.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import AgentSummary, NewAgentRequest
from src.domain.agents import (
    AgentEvent,
    AgentStartContext,
    MessageComplete,
    MessageDelta,
    StatusChange,
    ToolCall,
    ToolResult,
)
from src.domain.models import Agent
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.dtos import AddAgentRequest
from src.domain.workstore.ports import WorkStore
from src.infrastructure.agents import StubAgentAdapter
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

    adapter = StubAgentAdapter(
        scripted_events=_demo_events(),
        delay_seconds=settings.stub_event_delay,
    )
    context = AgentStartContext(
        workdir=Path(f"/tmp/atelier/{work_slug}/{agent.slug}"),
        context_md="",
        model=payload.model,
        system_prompt="",
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


def _demo_events() -> list[AgentEvent]:
    """Canned sequence the stub adapter replays for the walking-skeleton
    end-to-end demo. Real adapters (STORY-011+) replace this entirely."""
    now = datetime.now(UTC)
    return [
        StatusChange(ts=now, status="thinking"),
        MessageDelta(ts=now, text="Hello! I'm "),
        MessageDelta(ts=now, text="a stub agent "),
        MessageDelta(ts=now, text="for the walking-skeleton."),
        MessageComplete(ts=now, text="Hello! I'm a stub agent for the walking-skeleton."),
        StatusChange(ts=now, status="thinking"),
        MessageDelta(ts=now, text="Let me try a tool call."),
        MessageComplete(ts=now, text="Let me try a tool call."),
        ToolCall(
            ts=now,
            tool_id="t-1",
            name="read_file",
            arguments={"path": "~/notes.md"},
        ),
        ToolResult(
            ts=now,
            tool_id="t-1",
            content="(simulated) file not found",
        ),
        MessageDelta(ts=now, text="Got "),
        MessageDelta(ts=now, text="the result. "),
        MessageDelta(ts=now, text="That's all for now."),
        MessageComplete(ts=now, text="Got the result. That's all for now."),
        StatusChange(ts=now, status="idle"),
    ]
