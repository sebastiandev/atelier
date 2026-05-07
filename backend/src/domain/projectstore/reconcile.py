"""Filesystem-canonical reconciliation for projects.

Walks ``<workspace>/projects/`` and brings the SQL index into agreement:
insert rows that exist on disk but not in DB, update rows whose DB state
differs from disk, delete rows that no longer exist on disk.

Must run BEFORE ``workstore.reconcile`` at startup — Work rows reference
projects by slug, and the FK constraint rejects inserts whose target
slug isn't yet in the projects table.

Pure-domain — depends only on ``ProjectRepository`` + ``ProjectFiles``
and is unit-tested with stubs.
"""

from dataclasses import dataclass, field

from src.domain.projectstore._serde import deserialize_project_record
from src.domain.projectstore.ports import ProjectFiles, ProjectRepository


@dataclass(frozen=True)
class ProjectReconcileReport:
    inserted: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped_unreadable: list[str] = field(default_factory=list)


def reconcile(
    repo: ProjectRepository, files: ProjectFiles
) -> ProjectReconcileReport:
    report = ProjectReconcileReport()

    fs_slugs = set(files.list_project_slugs())
    db_by_slug = {p.slug: p for p in repo.list_projects() if p.slug is not None}

    for fs_slug in sorted(fs_slugs):
        data = files.read_project_json(fs_slug)
        if data is None:
            report.skipped_unreadable.append(f"project:{fs_slug}")
            continue
        try:
            fs_project = deserialize_project_record(data)
        except (KeyError, ValueError, TypeError):
            report.skipped_unreadable.append(f"project:{fs_slug}")
            continue

        db_project = db_by_slug.get(fs_slug)
        if db_project is None:
            repo.upsert_project(fs_project)
            report.inserted.append(fs_slug)
        elif db_project != fs_project:
            repo.upsert_project(fs_project)
            report.updated.append(fs_slug)

    for db_slug in sorted(db_by_slug.keys() - fs_slugs):
        repo.delete_project(db_slug)
        report.deleted.append(db_slug)

    return report


__all__ = ["ProjectReconcileReport", "reconcile"]
