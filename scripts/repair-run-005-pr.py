#!/usr/bin/env python
"""One-off repair for WRK-002 / bug-typebuilder-hardening-gaps / run-005.

The Create PR stage opened https://github.com/Shiphero/Shiphero-API/pull/46245
and then called ``atelier__record_pr`` without a ``title``. The tracker
rejected the marker, the supervisor published it as a plain ``error``
event, ``turn_monitor._terminal_error`` read that as a dead provider, and
the loop monitor failed the run with ``provider_runtime`` -- before the
stage report was ever processed. So the PR exists on GitHub but Atelier
has no PrArtifact row, no ``loop.pr`` snapshot, and a failed stage.

The cause is fixed (derived titles + ``recoverable`` marker errors), but
that does not repair the run retroactively. This script replays the
completion the monitor never got to run, through the real domain code:

  1. Record the PrArtifact, attributed to the Create PR stage agent --
     ``pr_lifecycle._resolve_pr_completion`` resolves the PR from the
     artifacts owned by that agent.
  2. Call ``pr_lifecycle.capture_completion`` so the ``pr`` snapshot,
     ``push_at`` and pass sealing are produced by production code rather
     than hand-written JSON.
  3. Mark the stage passed and finish the loop via ``_complete_pr``, the
     same function the monitor uses for ``pass -> complete`` on a PR
     stage that follows an approved run.

Saving goes through PlanningLoopRunStore, which dual-writes the plan
manifest and the loop_runs row -- raw SQL would desync them.

Idempotent: artifact recording de-dupes on URL, and the script exits
early if the stage is already passed. Safe to run with the backend up;
the run is failed, so no monitor task holds it.

    uv run --extra dev python ../scripts/repair-run-005-pr.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from src.domain.loop import pr_lifecycle  # noqa: E402
from src.domain.loop.monitor import _complete_pr  # noqa: E402
from src.domain.planning.loop_store import PlanningLoopRunStore  # noqa: E402
from src.domain.workstore import WorkStoreService  # noqa: E402
from src.domain.workstore.dtos import RecordArtifactRequest  # noqa: E402
from src.infrastructure.database import (  # noqa: E402
    SqlLoopRunRepository,
    SqlWorkRepository,
    configure_mappings,
    create_database_engine,
    create_session_factory,
)
from src.infrastructure.filesystem import (  # noqa: E402
    FsPlanningFiles,
    FsTranscriptLog,
    FsWorkspaceFiles,
    WorkspacePaths,
)
from src.settings import get_settings  # noqa: E402

WORK_SLUG = "WRK-002"
ARTIFACT_ID = "bug-typebuilder-hardening-gaps"
RUN_ID = "run-005"
STAGE_ID = "create-pr"
PR_URL = "https://github.com/Shiphero/Shiphero-API/pull/46245"
PR_REPO = "Shiphero/Shiphero-API"
# What the stage was configured to name the PR (loop.pr_config.name).
PR_TITLE = "Close Verified TypeBuilder Correctness Gaps"
SUMMARY = (
    "Committed, pushed, and opened PR #46245. Recorded retroactively: the "
    "original artifact marker was rejected for a missing title."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_database_engine(settings)
    configure_mappings()
    session_factory = create_session_factory(engine)
    paths = WorkspacePaths(workspace_root=settings.workspace_root)

    workstore = WorkStoreService(
        SqlWorkRepository(session_factory),
        FsWorkspaceFiles(paths),
        FsTranscriptLog(paths),
    )
    store = PlanningLoopRunStore(
        FsPlanningFiles(paths), SqlLoopRunRepository(session_factory), ARTIFACT_ID
    )

    target = store.load(WORK_SLUG, RUN_ID)
    if target is None:
        print(f"run {RUN_ID} not found for {WORK_SLUG}/{ARTIFACT_ID}")
        return 1

    run = target.run
    loop = run["loop"]
    stage_row = next(
        (row for row in loop["stages"] if row.get("id") == STAGE_ID), None
    )
    if stage_row is None:
        print(f"stage {STAGE_ID} not found")
        return 1

    print(f"before: run={run['status']} loop={loop['status']} stage={stage_row['status']}")
    print(f"        failure_kind={loop.get('failure_kind')!r}")
    print(f"        loop.pr={loop.get('pr')!r}")

    if stage_row.get("status") == "passed":
        print("stage already passed — nothing to repair")
        return 0

    agent_slug = stage_row.get("agent_slug")
    if not agent_slug:
        print("stage has no agent_slug; cannot attribute the PR artifact")
        return 1

    if not args.apply:
        print("\n-- dry run; re-run with --apply to write --")
        print(f"would record PrArtifact {PR_URL} for {agent_slug}")
        print(f"would pass stage {STAGE_ID} and accept the run")
        return 0

    backup = Path(f"{settings.workspace_root}/atelier.db").with_suffix(
        f".db.bak-{datetime.now(UTC):%Y%m%d%H%M%S}"
    )
    shutil.copy2(Path(settings.workspace_root) / "atelier.db", backup)
    print(f"\nbacked up database to {backup}")

    artifact = workstore.record_artifact(
        RecordArtifactRequest(
            work_slug=WORK_SLUG,
            agent_slug=agent_slug,
            type="pr",
            title=PR_TITLE,
            status="open",
            url=PR_URL,
            repo=PR_REPO,
        )
    )
    print(f"recorded artifact {artifact.slug}: {artifact.title}")

    stage_row["status"] = "passed"
    stage_row["summary"] = SUMMARY
    pr_lifecycle.capture_completion(workstore, target, stage_row, (PR_URL,))
    loop.pop("failure_kind", None)
    loop["findings"] = []
    _complete_pr(run, loop, STAGE_ID)
    store.save(target)

    print(f"after:  run={run['status']} loop={loop['status']} stage={stage_row['status']}")
    print(f"        loop.pr={json.dumps(loop.get('pr'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
