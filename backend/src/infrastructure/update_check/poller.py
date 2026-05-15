"""Background loop that checks for upstream updates on a 2h cadence.

Mirrors the shape of ``PrStatusPoller``: lifecycle owned by the FastAPI
lifespan; loop sleeps first so backend startup is quiet; failures are
caught + logged so a wedged remote can't take the loop down.

The poller owns the canonical ``UpdateStatus`` for the process: callers
(the HTTP route) read it from ``poller.status``. There is no shared
queue — only one consumer (the route) and one producer (this loop), so
plain attribute assignment is enough.
"""

from __future__ import annotations

import asyncio
import logging

from src.domain.update_check import UpdateChecker, UpdateStatus

_log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 2 * 60 * 60  # 2 hours


class UpdateCheckPoller:
    def __init__(
        self,
        checker: UpdateChecker,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._checker = checker
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._status: UpdateStatus | None = None

    @property
    def status(self) -> UpdateStatus | None:
        return self._status

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="update-check-poller")

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

    async def _loop(self) -> None:
        # One immediate check at boot so users who restart the backend
        # after an out-of-date period see the chip without waiting two
        # hours. Failures here are swallowed by the checker.
        try:
            initial = await self._checker()
            if initial is not None:
                self._status = initial
        except Exception:
            _log.exception("update-check initial cycle failed")

        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._interval
                    )
                    return
                except asyncio.TimeoutError:
                    pass

                try:
                    status = await self._checker()
                except Exception:
                    _log.exception("update-check cycle failed")
                    continue
                if status is not None:
                    self._status = status
                    if status.available:
                        _log.info(
                            "update-check: behind (current=%s latest=%s)",
                            status.current_sha[:8], status.latest_sha[:8],
                        )
        except asyncio.CancelledError:
            raise


__all__ = ["DEFAULT_INTERVAL_SECONDS", "UpdateCheckPoller"]
