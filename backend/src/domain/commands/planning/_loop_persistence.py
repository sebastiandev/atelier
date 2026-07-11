"""Shared projection from Planning manifests into generic loop persistence."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from src.domain.loop.dtos import LoopStatus, LoopStepKind, LoopStepStatus, LoopTargetKind
from src.domain.loop.models import LoopRunRecord, LoopStepRunRecord
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactSummary


def persist_artifact_run(
    repository: LoopRunRepository,
    *,
    work_slug: str,
    artifact: PlanArtifactSummary,
    run: dict[str, Any],
) -> None:
    """Persist one Planning run in the shared loop index.

    Preconditions: ``run`` is the manifest row for ``artifact``.
    Postconditions: the SQL run and stage snapshots match the manifest row.
    """
    loop = actions.dict_or_empty(run.get("loop"))
    run_key = actions.str_or_empty(loop.get("loop_run_id"))
    if not run_key:
        return
    now = datetime.now(UTC)
    status = _loop_status(loop.get("status"))
    completed_at = _date(run.get("completed_at"))
    cleanup_at = _date(run.get("cleanup_at"))
    record = LoopRunRecord(
        run_key=run_key,
        work_slug=work_slug,
        target_kind=LoopTargetKind.PLANNING_ARTIFACT,
        target_ref=artifact.source_ref,
        artifact_id=artifact.id,
        plan_run_id=actions.str_or_none(run.get("id")),
        definition_id=actions.str_or_empty(loop.get("definition_id")),
        definition_revision=actions.str_or_empty(loop.get("definition_revision")),
        definition_snapshot=deepcopy(actions.dict_or_empty(loop.get("definition_snapshot"))),
        status=status,
        current_step_id=actions.str_or_none(loop.get("current_stage_id")),
        state=deepcopy(run),
        started_at=_date(run.get("started_at")) or now,
        updated_at=now,
        completed_at=completed_at,
        accepted_at=(
            completed_at
            if status in {LoopStatus.ACCEPTED, LoopStatus.CLEANED}
            else None
        ),
        cleanup_at=cleanup_at,
    )
    repository.upsert(record, _stage_records(run_key, loop, now))


def _stage_records(
    run_key: str,
    loop: dict[str, Any],
    now: datetime,
) -> tuple[LoopStepRunRecord, ...]:
    raw = loop.get("stages")
    if not isinstance(raw, list):
        return ()
    rows: list[LoopStepRunRecord] = []
    for stage in raw:
        if not isinstance(stage, dict):
            continue
        try:
            kind = LoopStepKind(actions.str_or_empty(stage.get("kind")))
            status = LoopStepStatus(actions.str_or_empty(stage.get("status")))
        except ValueError:
            continue
        rows.append(
            LoopStepRunRecord(
                run_key=run_key,
                step_id=actions.str_or_empty(stage.get("id")),
                kind=kind,
                status=status,
                attempt=actions.int_or_default(stage.get("attempt"), 0),
                agent_slug=actions.str_or_none(stage.get("agent_slug")),
                cursor=_stage_cursor(stage, loop),
                state=deepcopy(stage),
                updated_at=now,
            )
        )
    return tuple(row for row in rows if row.step_id)


def _stage_cursor(stage: dict[str, Any], loop: dict[str, Any]) -> int:
    reports = stage.get("reports")
    if isinstance(reports, list) and reports:
        latest = reports[-1]
        if isinstance(latest, dict):
            return actions.int_or_default(latest.get("seq"), 0)
    if stage.get("id") == loop.get("current_stage_id"):
        return actions.int_or_default(loop.get("last_checked_seq"), 0)
    return 0


def _loop_status(value: object) -> LoopStatus:
    try:
        return LoopStatus(value) if isinstance(value, str) else LoopStatus.PENDING
    except ValueError:
        return LoopStatus.PENDING


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


__all__ = ["persist_artifact_run"]
