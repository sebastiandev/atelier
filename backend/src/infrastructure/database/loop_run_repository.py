"""SQLAlchemy adapter for durable loop-run and stage snapshots."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.domain.loop.dtos import LoopStatus, LoopTargetKind
from src.domain.loop.models import LoopRunRecord, LoopStepRunRecord
from src.infrastructure.database.tables import loop_runs_table, loop_step_runs_table


class SqlLoopRunRepository:
    """Store current run state and one aggregate row per stage."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @contextmanager
    def _txn(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def upsert(
        self,
        run: LoopRunRecord,
        steps: tuple[LoopStepRunRecord, ...],
    ) -> None:
        with self._txn() as session:
            run_id = session.execute(
                select(loop_runs_table.c.id).where(
                    loop_runs_table.c.work_slug == run.work_slug,
                    loop_runs_table.c.run_key == run.run_key,
                )
            ).scalar_one_or_none()
            values = _run_values(run)
            if run_id is None:
                session.execute(loop_runs_table.insert().values(**values))
                run_id = session.execute(
                    select(loop_runs_table.c.id).where(
                        loop_runs_table.c.work_slug == run.work_slug,
                        loop_runs_table.c.run_key == run.run_key,
                    )
                ).scalar_one()
            else:
                values.pop("lease_owner")
                values.pop("lease_expires_at")
                session.execute(
                    update(loop_runs_table)
                    .where(loop_runs_table.c.id == run_id)
                    .values(**values)
                )

            keep: set[str] = set()
            for step in steps:
                keep.add(step.step_id)
                step_id = session.execute(
                    select(loop_step_runs_table.c.id).where(
                        loop_step_runs_table.c.loop_run_id == run_id,
                        loop_step_runs_table.c.step_id == step.step_id,
                    )
                ).scalar_one_or_none()
                step_values = _step_values(step, run_id)
                if step_id is None:
                    session.execute(loop_step_runs_table.insert().values(**step_values))
                else:
                    session.execute(
                        update(loop_step_runs_table)
                        .where(loop_step_runs_table.c.id == step_id)
                        .values(**step_values)
                    )
            if keep:
                session.execute(
                    delete(loop_step_runs_table).where(
                        loop_step_runs_table.c.loop_run_id == run_id,
                        loop_step_runs_table.c.step_id.not_in(keep),
                    )
                )

    def get(self, work_slug: str, run_key: str) -> LoopRunRecord | None:
        with self._txn() as session:
            row = session.execute(
                select(loop_runs_table).where(
                    loop_runs_table.c.work_slug == work_slug,
                    loop_runs_table.c.run_key == run_key,
                )
            ).mappings().one_or_none()
            return _to_run(row) if row is not None else None

    def list_for_work(self, work_slug: str) -> list[LoopRunRecord]:
        with self._txn() as session:
            rows = session.execute(
                select(loop_runs_table)
                .where(loop_runs_table.c.work_slug == work_slug)
                .order_by(loop_runs_table.c.id)
            ).mappings()
            return [_to_run(row) for row in rows]

    def list_active(self) -> list[LoopRunRecord]:
        terminal = {
            LoopStatus.ACCEPTED.value,
            LoopStatus.CANCELLED.value,
            LoopStatus.CLEANED.value,
            LoopStatus.FAILED.value,
        }
        with self._txn() as session:
            rows = session.execute(
                select(loop_runs_table)
                .where(loop_runs_table.c.status.not_in(terminal))
                .order_by(loop_runs_table.c.id)
            ).mappings()
            return [_to_run(row) for row in rows]

    def claim(
        self,
        work_slug: str,
        run_key: str,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> bool:
        now = datetime.now(UTC)
        with self._txn() as session:
            session.execute(
                update(loop_runs_table)
                .where(
                    loop_runs_table.c.work_slug == work_slug,
                    loop_runs_table.c.run_key == run_key,
                    (
                        loop_runs_table.c.lease_owner.is_(None)
                        | (loop_runs_table.c.lease_owner == worker_id)
                        | (loop_runs_table.c.lease_expires_at < now)
                    ),
                )
                .values(
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                )
            )
            owner = session.execute(
                select(loop_runs_table.c.lease_owner).where(
                    loop_runs_table.c.work_slug == work_slug,
                    loop_runs_table.c.run_key == run_key,
                )
            ).scalar_one_or_none()
            return owner == worker_id

    def release(self, work_slug: str, run_key: str, worker_id: str) -> None:
        with self._txn() as session:
            session.execute(
                update(loop_runs_table)
                .where(
                    loop_runs_table.c.work_slug == work_slug,
                    loop_runs_table.c.run_key == run_key,
                    loop_runs_table.c.lease_owner == worker_id,
                )
                .values(lease_owner=None, lease_expires_at=None)
            )


def _run_values(run: LoopRunRecord) -> dict[str, object]:
    return {
        "run_key": run.run_key,
        "work_slug": run.work_slug,
        "target_kind": run.target_kind.value,
        "target_ref": run.target_ref,
        "artifact_id": run.artifact_id,
        "plan_run_id": run.plan_run_id,
        "definition_id": run.definition_id,
        "definition_revision": run.definition_revision,
        "definition_snapshot": run.definition_snapshot,
        "status": run.status.value,
        "current_step_id": run.current_step_id,
        "state": run.state,
        "started_at": run.started_at,
        "updated_at": run.updated_at,
        "completed_at": run.completed_at,
        "accepted_at": run.accepted_at,
        "cancelled_at": run.cancelled_at,
        "cleanup_at": run.cleanup_at,
        "lease_owner": run.lease_owner,
        "lease_expires_at": run.lease_expires_at,
    }


def _step_values(step: LoopStepRunRecord, run_id: int) -> dict[str, object]:
    return {
        "loop_run_id": run_id,
        "step_id": step.step_id,
        "kind": step.kind.value,
        "status": step.status.value,
        "attempt": step.attempt,
        "agent_slug": step.agent_slug,
        "cursor": step.cursor,
        "state": step.state,
        "updated_at": step.updated_at,
        "lease_owner": step.lease_owner,
        "lease_expires_at": step.lease_expires_at,
    }


def _to_run(row: object) -> LoopRunRecord:
    values = row
    return LoopRunRecord(
        id=values["id"],  # type: ignore[index]
        run_key=values["run_key"],  # type: ignore[index]
        work_slug=values["work_slug"],  # type: ignore[index]
        target_kind=LoopTargetKind(values["target_kind"]),  # type: ignore[index]
        target_ref=values["target_ref"],  # type: ignore[index]
        artifact_id=values["artifact_id"],  # type: ignore[index]
        plan_run_id=values["plan_run_id"],  # type: ignore[index]
        definition_id=values["definition_id"],  # type: ignore[index]
        definition_revision=values["definition_revision"],  # type: ignore[index]
        definition_snapshot=values["definition_snapshot"],  # type: ignore[index]
        status=LoopStatus(values["status"]),  # type: ignore[index]
        current_step_id=values["current_step_id"],  # type: ignore[index]
        state=values["state"],  # type: ignore[index]
        started_at=values["started_at"],  # type: ignore[index]
        updated_at=values["updated_at"],  # type: ignore[index]
        completed_at=values["completed_at"],  # type: ignore[index]
        accepted_at=values["accepted_at"],  # type: ignore[index]
        cancelled_at=values["cancelled_at"],  # type: ignore[index]
        cleanup_at=values["cleanup_at"],  # type: ignore[index]
        lease_owner=values["lease_owner"],  # type: ignore[index]
        lease_expires_at=values["lease_expires_at"],  # type: ignore[index]
    )


__all__ = ["SqlLoopRunRepository"]
