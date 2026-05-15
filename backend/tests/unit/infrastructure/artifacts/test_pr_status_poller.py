"""Tests for ``PrStatusPoller.refresh_now`` throttle behaviour.

The scheduled loop is exercised end-to-end by existing integration
tests; here we focus on the on-demand refresh path that the
work-view-load endpoint hits.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.domain.commands.artifacts.refresh_pr_statuses import RefreshResult
from src.infrastructure.artifacts.pr_status_poller import PrStatusPoller


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_poller() -> PrStatusPoller:
    """Build a poller with the bare minimum to call ``refresh_now``
    without running the scheduled loop. We never start it — the loop
    isn't what these tests are about."""
    poller = PrStatusPoller(
        workstore=object(),  # type: ignore[arg-type]
        interval_seconds=999999,
        throttle_seconds=30.0,
    )
    # Stand in for what ``start()`` would have built — a real client
    # would be wasteful since we patch ``refresh_pr_statuses.execute``.
    poller._client = httpx.AsyncClient()
    return poller


@pytest.mark.anyio
async def test_refresh_now_runs_on_first_call() -> None:
    poller = _make_poller()
    fake_result = RefreshResult(checked=2, updated=1, skipped=0, not_modified=0)
    with patch(
        "src.infrastructure.artifacts.pr_status_poller.refresh_pr_statuses.execute",
        new=AsyncMock(return_value=fake_result),
    ) as exec_mock:
        result = await poller.refresh_now()
    assert result == fake_result
    assert exec_mock.await_count == 1
    await poller._client.aclose()  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_refresh_now_throttles_repeat_calls_within_window() -> None:
    """A second call inside the throttle window returns None without
    invoking the underlying refresh — prevents the user from triggering
    a fetch storm by bouncing between work tabs."""
    poller = _make_poller()
    fake_result = RefreshResult(checked=1, updated=0, skipped=0, not_modified=1)
    with patch(
        "src.infrastructure.artifacts.pr_status_poller.refresh_pr_statuses.execute",
        new=AsyncMock(return_value=fake_result),
    ) as exec_mock:
        first = await poller.refresh_now()
        second = await poller.refresh_now()
    assert first == fake_result
    assert second is None
    assert exec_mock.await_count == 1
    await poller._client.aclose()  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_refresh_now_runs_again_after_throttle_window() -> None:
    poller = _make_poller()
    poller._throttle = 0.05  # 50ms — fast enough to wait through in tests

    fake_result = RefreshResult(checked=1, updated=0, skipped=0, not_modified=0)
    with patch(
        "src.infrastructure.artifacts.pr_status_poller.refresh_pr_statuses.execute",
        new=AsyncMock(return_value=fake_result),
    ) as exec_mock:
        await poller.refresh_now()
        await asyncio.sleep(0.08)
        second = await poller.refresh_now()
    assert second == fake_result
    assert exec_mock.await_count == 2
    await poller._client.aclose()  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_refresh_now_returns_none_when_poller_not_started() -> None:
    """No httpx client means ``start()`` hasn't run — likely a test
    fixture or mid-shutdown. Refuse rather than allocate something we
    won't get to clean up."""
    poller = PrStatusPoller(workstore=object(), throttle_seconds=30.0)  # type: ignore[arg-type]
    assert poller._client is None
    assert await poller.refresh_now() is None


@pytest.mark.anyio
async def test_refresh_now_swallows_execute_exceptions() -> None:
    """A bug in the refresh path mustn't propagate to the route — the
    poller logs + returns None so the FE just sees ``ran=False``."""
    poller = _make_poller()
    with patch(
        "src.infrastructure.artifacts.pr_status_poller.refresh_pr_statuses.execute",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await poller.refresh_now()
    assert result is None
    await poller._client.aclose()  # type: ignore[union-attr]
