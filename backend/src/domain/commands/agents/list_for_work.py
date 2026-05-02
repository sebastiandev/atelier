"""List Agents belonging to a Work."""

from src.domain.models import Agent
from src.domain.workstore.ports import WorkStore


def execute(workstore: WorkStore, work_slug: str) -> list[Agent]:
    return workstore.list_agents_for_work(work_slug)
