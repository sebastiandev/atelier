#!/usr/bin/env python3
"""Wipe Atelier state.

Destructive. Stop the backend before running, otherwise the supervisor's
in-flight writes will race the deletes.

Connections (DB rows + keychain entries) and schema_version are preserved.

Usage (via the wrapper, which handles uv + venv):

    ./scripts/wipe.sh all                 # every work + every project + FS
    ./scripts/wipe.sh work WRK-001        # one work + its FS folder
    ./scripts/wipe.sh project PRJ-001     # a project and all its works

Or directly, from inside backend/ with the backend venv active:

    uv run python ../scripts/wipe.py all -y
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import delete, func, select  # noqa: E402

from src.infrastructure.database.engine import create_database_engine  # noqa: E402
from src.infrastructure.database.tables import (  # noqa: E402
    projects_table,
    works_table,
)
from src.settings import get_settings  # noqa: E402


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _count(conn, table) -> int:
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def cmd_all(args: argparse.Namespace) -> None:
    settings = get_settings()
    workspace = settings.workspace_root
    works_dir = workspace / "works"
    projects_dir = workspace / "projects"
    engine = create_database_engine(settings)

    with engine.connect() as conn:
        n_works = _count(conn, works_table)
        n_projects = _count(conn, projects_table)

    print("Atelier wipe — deletes ALL works and projects.\n")
    print(f"  Works:      {n_works} (cascades to agents / artifacts / handoffs / transcripts)")
    print(f"  Projects:   {n_projects}")
    print(f"  Filesystem: {works_dir}")
    print(f"              {projects_dir}")
    print("\nPreserved: connections (DB + keychain), schema_version.\n")

    if (
        n_works == 0
        and n_projects == 0
        and not works_dir.exists()
        and not projects_dir.exists()
    ):
        print("Already empty. Nothing to do.")
        return

    if not args.yes and not _confirm("Continue?"):
        print("Aborted.")
        sys.exit(1)

    with engine.begin() as conn:
        conn.execute(delete(works_table))
        conn.execute(delete(projects_table))

    for d in (works_dir, projects_dir):
        if d.exists():
            shutil.rmtree(d)

    engine.dispose()
    print("Done.")


def cmd_work(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = create_database_engine(settings)
    workspace = settings.workspace_root

    with engine.connect() as conn:
        row = conn.execute(
            select(works_table.c.name).where(works_table.c.slug == args.slug)
        ).one_or_none()

    if row is None:
        print(f"No work with slug {args.slug!r}.", file=sys.stderr)
        sys.exit(1)
    work_name = row[0]
    work_dir = workspace / "works" / args.slug

    print(f"Atelier wipe — deletes work {args.slug} ({work_name!r}).\n")
    print("  DB row + cascading agents / artifacts / handoffs / transcripts")
    print(f"  Filesystem: {work_dir}")
    print()

    if not args.yes and not _confirm("Continue?"):
        print("Aborted.")
        sys.exit(1)

    with engine.begin() as conn:
        conn.execute(delete(works_table).where(works_table.c.slug == args.slug))

    if work_dir.exists():
        shutil.rmtree(work_dir)

    engine.dispose()
    print("Done.")


def cmd_project(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = create_database_engine(settings)
    workspace = settings.workspace_root

    with engine.connect() as conn:
        proj = conn.execute(
            select(projects_table.c.name).where(projects_table.c.slug == args.slug)
        ).one_or_none()
        if proj is None:
            print(f"No project with slug {args.slug!r}.", file=sys.stderr)
            sys.exit(1)
        proj_name = proj[0]

        work_slugs = [
            r[0]
            for r in conn.execute(
                select(works_table.c.slug).where(works_table.c.project_slug == args.slug)
            ).all()
        ]

    project_dir = workspace / "projects" / args.slug
    print(f"Atelier wipe — deletes project {args.slug} ({proj_name!r}).\n")
    print("  Project row")
    print(f"  {len(work_slugs)} works in the project (cascades to children + transcripts)")
    for s in work_slugs[:5]:
        print(f"    - {s}")
    if len(work_slugs) > 5:
        print(f"    + {len(work_slugs) - 5} more")
    print("  Filesystem: <workspace>/works/<slug>/ for each")
    print(f"              {project_dir}")
    print()

    if not args.yes and not _confirm("Continue?"):
        print("Aborted.")
        sys.exit(1)

    with engine.begin() as conn:
        if work_slugs:
            conn.execute(
                delete(works_table).where(works_table.c.project_slug == args.slug)
            )
        conn.execute(delete(projects_table).where(projects_table.c.slug == args.slug))

    for s in work_slugs:
        work_dir = workspace / "works" / s
        if work_dir.exists():
            shutil.rmtree(work_dir)
    if project_dir.exists():
        shutil.rmtree(project_dir)

    engine.dispose()
    print("Done.")


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt."
    )

    parser = argparse.ArgumentParser(
        description="Wipe Atelier state. Destructive — stop the backend before running.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_all = sub.add_parser(
        "all", parents=[common], help="Delete every work and project (and FS)."
    )
    p_all.set_defaults(func=cmd_all)

    p_work = sub.add_parser(
        "work", parents=[common], help="Delete a single work by slug."
    )
    p_work.add_argument("slug", help="Work slug, e.g. WRK-001")
    p_work.set_defaults(func=cmd_work)

    p_proj = sub.add_parser(
        "project", parents=[common], help="Delete a project and all its works."
    )
    p_proj.add_argument("slug", help="Project slug, e.g. PRJ-001")
    p_proj.set_defaults(func=cmd_project)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
