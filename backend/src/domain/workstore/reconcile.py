"""Filesystem-canonical reconciliation.

Walks `~/Atelier/works/` and brings the SQL index into agreement: insert
rows that exist on disk but not in DB, update rows whose DB state differs
from disk, delete rows that no longer exist on disk. Work deletion
cascades to agents through SQL FK; we still walk per-work agent dirs so
mismatches *within* a still-present work get fixed.

Pure-domain — depends only on `WorkRepository` and `WorkspaceFiles` ports
and is unit-tested with stubs.
"""

from dataclasses import dataclass, field

from src.domain.workstore._serde import (
    deserialize_agent,
    deserialize_work_record,
)
from src.domain.workstore.ports import WorkRepository, WorkspaceFiles


@dataclass(frozen=True)
class ReconcileReport:
    """What reconcile changed. Used by tests; logged at startup."""

    inserted_works: list[str] = field(default_factory=list)
    updated_works: list[str] = field(default_factory=list)
    deleted_works: list[str] = field(default_factory=list)
    inserted_agents: list[str] = field(default_factory=list)
    updated_agents: list[str] = field(default_factory=list)
    deleted_agents: list[str] = field(default_factory=list)
    skipped_unreadable: list[str] = field(default_factory=list)


def reconcile(repo: WorkRepository, files: WorkspaceFiles) -> ReconcileReport:
    report = ReconcileReport()

    fs_work_slugs = set(files.list_work_slugs())
    db_works_by_slug = {w.slug: w for w in repo.list_works() if w.slug is not None}

    for fs_slug in sorted(fs_work_slugs):
        data = files.read_work_json(fs_slug)
        if data is None:
            report.skipped_unreadable.append(f"work:{fs_slug}")
            continue
        try:
            fs_work, _ = deserialize_work_record(data)
        except (KeyError, ValueError, TypeError):
            report.skipped_unreadable.append(f"work:{fs_slug}")
            continue

        db_work = db_works_by_slug.get(fs_slug)
        if db_work is None:
            repo.upsert_work(fs_work)
            report.inserted_works.append(fs_slug)
        elif db_work != fs_work:
            repo.upsert_work(fs_work)
            report.updated_works.append(fs_slug)

        _reconcile_agents_for_work(repo, files, fs_slug, report)

    for db_slug in sorted(db_works_by_slug.keys() - fs_work_slugs):
        repo.delete_work(db_slug)
        report.deleted_works.append(db_slug)

    return report


def _reconcile_agents_for_work(
    repo: WorkRepository,
    files: WorkspaceFiles,
    work_slug: str,
    report: ReconcileReport,
) -> None:
    fs_agent_slugs = set(files.list_agent_slugs(work_slug))
    db_agents_by_slug = {
        a.slug: a for a in repo.list_agents_for_work(work_slug) if a.slug is not None
    }

    for fs_slug in sorted(fs_agent_slugs):
        data = files.read_agent_json(work_slug, fs_slug)
        if data is None:
            report.skipped_unreadable.append(f"agent:{work_slug}/{fs_slug}")
            continue
        try:
            fs_agent = deserialize_agent(data)
        except (KeyError, ValueError, TypeError):
            report.skipped_unreadable.append(f"agent:{work_slug}/{fs_slug}")
            continue

        db_agent = db_agents_by_slug.get(fs_slug)
        if db_agent is None:
            repo.upsert_agent(fs_agent)
            report.inserted_agents.append(fs_slug)
        elif db_agent != fs_agent:
            repo.upsert_agent(fs_agent)
            report.updated_agents.append(fs_slug)

    for db_slug in sorted(db_agents_by_slug.keys() - fs_agent_slugs):
        repo.delete_agent(db_slug)
        report.deleted_agents.append(db_slug)


__all__ = ["ReconcileReport", "reconcile"]
