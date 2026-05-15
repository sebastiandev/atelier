"""Tests for ``UpdateCheckPoller`` lifecycle + error handling."""

from __future__ import annotations

import asyncio

import pytest

from src.domain.update_check import UpdateStatus
from src.infrastructure.update_check.poller import UpdateCheckPoller


@pytest.mark.anyio
async def test_initial_status_is_set_on_start() -> None:
    status = UpdateStatus(
        available=True, current_sha="a", latest_sha="b", repo_path="/x"
    )

    async def checker() -> UpdateStatus | None:
        return status

    poller = UpdateCheckPoller(checker, interval_seconds=3600)
    poller.start()
    for _ in range(20):
        if poller.status is not None:
            break
        await asyncio.sleep(0.01)
    assert poller.status == status
    await poller.stop()


@pytest.mark.anyio
async def test_checker_exception_does_not_crash_loop() -> None:
    calls = 0

    async def checker() -> UpdateStatus | None:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    poller = UpdateCheckPoller(checker, interval_seconds=3600)
    poller.start()
    for _ in range(20):
        if calls > 0:
            break
        await asyncio.sleep(0.01)
    assert calls >= 1
    assert poller.status is None
    await poller.stop()


@pytest.mark.anyio
async def test_stop_is_idempotent() -> None:
    async def checker() -> UpdateStatus | None:
        return None

    poller = UpdateCheckPoller(checker, interval_seconds=3600)
    poller.start()
    await poller.stop()
    await poller.stop()


@pytest.mark.anyio
async def test_none_from_checker_preserves_last_good_status() -> None:
    snapshots: list[UpdateStatus | None] = [
        UpdateStatus(available=True, current_sha="a", latest_sha="b", repo_path="/x"),
        None,
    ]

    async def checker() -> UpdateStatus | None:
        return snapshots.pop(0) if snapshots else None

    poller = UpdateCheckPoller(checker, interval_seconds=1)
    poller.start()
    for _ in range(30):
        if poller.status is not None:
            break
        await asyncio.sleep(0.01)
    assert poller.status is not None
    await asyncio.sleep(1.2)
    assert poller.status is not None
    assert poller.status.available is True
    await poller.stop()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
