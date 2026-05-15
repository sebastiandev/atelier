"""Background loop that refreshes PR statuses every 5 minutes.

Lifecycle is owned by the FastAPI lifespan: ``start()`` spawns the
loop as an ``asyncio.Task``; ``stop()`` cancels and awaits cleanly.

Design notes:

- The loop **never holds the workstore lock** between cycles — the
  command opens its own short window via ``asyncio.to_thread``.
- A cycle is a no-op when there are no non-terminal PRs: the
  workstore query short-circuits, the fetcher is never called, and
  ``gh auth token`` is never invoked. So a user who doesn't track
  any PRs pays no recurring cost beyond the sleeping coroutine.
- The first cycle waits the full interval — startup is busy enough
  without us hammering GitHub before the user has even loaded a tab.
- Unexpected errors inside a cycle are logged and swallowed; the
  loop keeps ticking. We'd rather miss a refresh than crash the
  whole background task and silently stop polling for the rest of
  the process's lifetime.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from src.domain.commands.artifacts import refresh_pr_statuses
from src.domain.workstore.ports import WorkStore
from src.infrastructure.artifacts.github_pr_status import GitHubPrStateFetcher

_log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300  # 5 minutes
# Minimum gap between out-of-band refreshes. Protects against a user
# bouncing between work tabs (or scripts hammering the endpoint) from
# triggering one fetch-per-PR-per-click. 30s is short enough that an
# opened tab feels live, long enough to coalesce real bursts.
DEFAULT_THROTTLE_SECONDS = 30.0


class PrStatusPoller:
    def __init__(
        self,
        workstore: WorkStore,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    ) -> None:
        self._workstore = workstore
        self._interval = interval_seconds
        self._throttle = throttle_seconds
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None
        self._stop_event = asyncio.Event()
        # Last refresh wall-clock (monotonic). Set after the scheduled
        # loop or an out-of-band call finishes; used to throttle
        # ``refresh_now``. Float('-inf') so the first call always runs.
        self._last_refresh_monotonic: float = float("-inf")
        # Serialise concurrent ``refresh_now`` invocations — two
        # browser tabs opening simultaneously shouldn't double-fetch.
        self._refresh_lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is not None:
            return
        # One client per poller — shared across cycles so connection
        # reuse + keep-alive work. Bounded so a wedged GitHub doesn't
        # let connections pile up forever.
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._task = asyncio.create_task(self._loop(), name="pr-status-poller")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def refresh_now(self) -> "refresh_pr_statuses.RefreshResult | None":
        """Out-of-band refresh triggered by the UI (work-view mount).

        Throttled to one run per ``throttle_seconds`` — concurrent
        callers within the window get ``None`` and the cached statuses
        from the last refresh. Reuses the same httpx client + fetcher
        as the scheduled loop so connection pooling carries over.
        """
        if self._client is None:
            # Poller not started (tests, or backend mid-shutdown).
            # Refuse rather than build a client we won't get to close.
            return None
        async with self._refresh_lock:
            now = asyncio.get_event_loop().time()
            if now - self._last_refresh_monotonic < self._throttle:
                return None
            fetcher = GitHubPrStateFetcher(self._client)
            try:
                result = await refresh_pr_statuses.execute(
                    self._workstore, fetcher
                )
            except Exception:
                _log.exception("on-demand pr-status refresh failed")
                return None
            self._last_refresh_monotonic = now
            if result.checked or result.updated:
                _log.info(
                    "pr-status (on-demand): checked=%d updated=%d "
                    "skipped=%d not_modified=%d",
                    result.checked, result.updated,
                    result.skipped, result.not_modified,
                )
            return result

    async def _loop(self) -> None:
        assert self._client is not None
        fetcher = GitHubPrStateFetcher(self._client)
        try:
            while not self._stop_event.is_set():
                # Sleep FIRST so startup is quiet — the user can boot
                # the backend without an immediate burst of network
                # calls. If the user wants an instant refresh, the
                # work-view will get one naturally on first agent open.
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._interval
                    )
                    # Event set → we're shutting down.
                    return
                except asyncio.TimeoutError:
                    pass

                async with self._refresh_lock:
                    try:
                        result = await refresh_pr_statuses.execute(
                            self._workstore, fetcher
                        )
                    except Exception:
                        # Never let a single failed cycle take the
                        # loop down. Log + continue; the next tick
                        # retries.
                        _log.exception("pr-status refresh cycle failed")
                        continue
                    # Sharing the throttle clock with ``refresh_now``
                    # means a scheduled cycle satisfies the throttle
                    # for the next 30s — a user reloading right after
                    # the loop ran gets the freshly-persisted data.
                    self._last_refresh_monotonic = (
                        asyncio.get_event_loop().time()
                    )

                if result.checked or result.updated:
                    _log.info(
                        "pr-status cycle: checked=%d updated=%d skipped=%d",
                        result.checked, result.updated, result.skipped,
                    )
        except asyncio.CancelledError:
            raise


__all__ = ["DEFAULT_INTERVAL_SECONDS", "PrStatusPoller"]
