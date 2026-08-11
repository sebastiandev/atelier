"""Background sweep that frees runtime slots whose process has exited.

A runtime normally evicts itself: when its adapter's event pump returns, the
supervisor drops the slot and kicks the subscriber, and the frontend's
reconnect rebuilds it. A wrapper that exits without the pump noticing leaves
the slot registered, so `connect` attaches to a corpse instead of rebuilding
and the turn hangs until someone presses Reconnect -- which is only
`stop_agent`, the one call that frees the slot.

This sweep asks each quiet runtime whether its process is still there and
evicts the ones that are gone, through the same path the pump uses. It never
decides on silence alone: a slow turn and a wedged one look identical from
out here, and evicting a live one throws away work in flight.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from src.domain.supervisor import AgentSupervisorService

_log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60


class RuntimeLivenessPoller:
    """Sweep one or more supervisors for runtimes whose process has exited."""

    def __init__(
        self,
        supervisors: tuple[AgentSupervisorService, ...],
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._supervisors = supervisors
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="runtime-liveness")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        # `CancelledError` is a `BaseException`, so `suppress(Exception)` does
        # not catch it -- and awaiting a task we just cancelled is exactly how
        # it surfaces. Letting it escape would abort the rest of shutdown.
        with suppress(asyncio.CancelledError, Exception):
            await task

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                pass
            for supervisor in self._supervisors:
                try:
                    await supervisor.sweep_dead_runtimes()
                except Exception:
                    # Opportunistic: a failed sweep must not end the loop, or
                    # one bad cycle disables recovery for the whole session.
                    _log.exception("runtime liveness sweep failed")


__all__ = ["DEFAULT_INTERVAL_SECONDS", "RuntimeLivenessPoller"]
