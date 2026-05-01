"""Apply a partial update to a Work."""

from src.domain.workstore.dtos import UpdateWorkRequest, WorkRecord
from src.domain.workstore.ports import WorkStore


def execute(workstore: WorkStore, req: UpdateWorkRequest) -> WorkRecord:
    return workstore.update_work(req)
