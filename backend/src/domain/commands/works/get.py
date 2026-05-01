"""Fetch a Work + its contexts."""

from src.domain.workstore.dtos import WorkRecord
from src.domain.workstore.ports import WorkStore


def execute(workstore: WorkStore, work_slug: str) -> WorkRecord | None:
    return workstore.get_work(work_slug)
