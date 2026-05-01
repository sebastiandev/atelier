"""List Works (excluding soft-deleted)."""

from src.domain.models import Work
from src.domain.workstore.ports import WorkStore


def execute(workstore: WorkStore) -> list[Work]:
    return workstore.list_works()
