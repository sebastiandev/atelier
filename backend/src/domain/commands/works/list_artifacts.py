"""List artifacts for a work."""

from __future__ import annotations

from src.domain.models import Artifact
from src.domain.workstore.ports import WorkStore


def execute(workstore: WorkStore, work_slug: str) -> list[Artifact]:
    return workstore.list_artifacts_for_work(work_slug)


__all__ = ["execute"]
