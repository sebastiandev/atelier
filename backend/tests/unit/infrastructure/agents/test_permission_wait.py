"""An unanswered permission prompt must not wedge the session forever."""

import asyncio

import pytest

from src.infrastructure.agents import permission_wait
from src.infrastructure.agents.permission_wait import await_permission_decision


async def _wait(fut: asyncio.Future[str], *, cancelled: str = "__cancelled__") -> str:
    return await await_permission_decision(
        fut,
        request_id="rid",
        tool_name="ExitPlanMode",
        cancelled=cancelled,
        expired="deny",
    )


def test_returns_the_users_decision() -> None:
    async def scenario() -> str:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        fut.set_result("allow")
        return await _wait(fut)

    assert asyncio.run(scenario()) == "allow"


def test_denies_when_the_prompt_is_never_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(permission_wait, "PERMISSION_DECISION_TIMEOUT_S", 0.01)

    async def scenario() -> str:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        return await _wait(fut)

    assert asyncio.run(scenario()) == "deny"


def test_expiry_leaves_the_future_resolvable_by_a_late_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapters guard on ``fut.done()``; expiry must not blow up a late answer."""
    monkeypatch.setattr(permission_wait, "PERMISSION_DECISION_TIMEOUT_S", 0.01)

    async def scenario() -> asyncio.Future[str]:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await _wait(fut)
        return fut

    assert asyncio.run(scenario()).done()


def test_returns_the_callers_sentinel_when_the_turn_is_torn_down() -> None:
    async def scenario() -> str:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        task = asyncio.ensure_future(_wait(fut, cancelled="__cancelled__"))
        await asyncio.sleep(0)
        fut.cancel()
        return await task

    assert asyncio.run(scenario()) == "__cancelled__"


def test_deadline_is_generous_enough_for_a_human() -> None:
    """A stuck-session backstop, not a politeness timer."""
    assert permission_wait.PERMISSION_DECISION_TIMEOUT_S >= 10 * 60
