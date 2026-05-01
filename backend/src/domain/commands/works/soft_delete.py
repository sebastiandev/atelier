"""Soft-delete a Work — marks status='deleted' on disk and in the DB.

The filesystem is preserved. Reconcile will not re-resurrect the row
because the work.json on disk also carries ``status="deleted"``.
"""

from src.domain.workstore.ports import WorkStore


def execute(workstore: WorkStore, work_slug: str) -> None:
    workstore.soft_delete_work(work_slug)
