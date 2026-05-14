"""Refresh PR artifact statuses against their remote source.

Pulls all non-terminal PR artifacts (``open`` / ``draft``) from the
workstore, fans out a fetch via the injected ``PrStateFetcher``, and
writes back any status that changed. No-ops cleanly when the pool is
empty — the caller (the 5-min poller) should check first to avoid the
async overhead of an empty cycle, but the command is safe to call
regardless.

Concurrency: a small bounded gather so a large pool of PRs doesn't
fire 30+ simultaneous requests at GitHub. The cap is intentionally
modest — even at 5 in flight, a 30-PR cycle finishes in ~6× the
single-request latency, well inside the 5-minute window.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from src.domain.artifacts.models import PrArtifact
from src.domain.artifacts.pr_status import PrStateFetcher, parse_pr_url
from src.domain.workstore.ports import WorkStore

_log = logging.getLogger(__name__)

# Modest fan-out cap. GitHub's rate budget (5k/hour authenticated) is
# nowhere near saturated even at this concurrency, but keeping it low
# also limits the burst we send when a user has many active works.
DEFAULT_CONCURRENCY = 5


@dataclass(frozen=True)
class RefreshResult:
    checked: int  # PRs we attempted to fetch (parseable URL + non-terminal)
    updated: int  # rows whose persisted status actually changed
    skipped: int  # rows we couldn't fetch (unparseable URL, network error)


async def execute(
    workstore: WorkStore,
    fetcher: PrStateFetcher,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> RefreshResult:
    """Single refresh pass. Returns a small audit dataclass — the
    poller logs it once per cycle so steady-state behaviour is visible
    without spamming the log on every row."""
    rows: list[tuple[str, PrArtifact]] = await asyncio.to_thread(
        workstore.list_non_terminal_pr_artifacts
    )
    if not rows:
        return RefreshResult(checked=0, updated=0, skipped=0)

    semaphore = asyncio.Semaphore(concurrency)

    async def _check_one(artifact: PrArtifact) -> tuple[PrArtifact, str | None]:
        async with semaphore:
            if artifact.url is None:
                return artifact, None
            ref = parse_pr_url(artifact.url)
            if ref is None:
                return artifact, None
            new_status = await fetcher(ref)
            return artifact, new_status

    tasks = [_check_one(artifact) for _, artifact in rows]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    checked = 0
    updated = 0
    skipped = 0
    for outcome in results:
        if isinstance(outcome, BaseException):
            # A bug in the fetcher (not a network error — those return
            # None) shouldn't kill the cycle. Log and continue so the
            # other rows still get checked.
            _log.exception("pr-status fetch task raised", exc_info=outcome)
            skipped += 1
            continue
        artifact, new_status = outcome
        if new_status is None:
            skipped += 1
            continue
        checked += 1
        if new_status != artifact.status:
            assert artifact.slug is not None
            await asyncio.to_thread(
                workstore.update_artifact_status, artifact.slug, new_status
            )
            updated += 1
            _log.info(
                "pr-status: %s %s → %s",
                artifact.slug, artifact.status, new_status,
            )
            # If the new status is terminal, the next cycle's
            # ``list_non_terminal_pr_artifacts`` query simply won't
            # return this row — no extra bookkeeping here.
    return RefreshResult(checked=checked, updated=updated, skipped=skipped)


__all__ = ["DEFAULT_CONCURRENCY", "RefreshResult", "execute"]
