"""Create a new Work."""

from src.domain.workstore.dtos import CreateWorkRequest, WorkRecord
from src.domain.workstore.ports import WorkStore


def execute(workstore: WorkStore, req: CreateWorkRequest) -> WorkRecord:
    return workstore.create_work(req)
