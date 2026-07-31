"""A permission prompt waits for the user, however long that takes."""

import asyncio

from src.infrastructure.agents.permission_wait import await_permission_decision


async def _wait(fut: asyncio.Future[str], *, cancelled: str = "__cancelled__") -> str:
    return await await_permission_decision(
        fut,
        request_id="rid",
        tool_name="ExitPlanMode",
        cancelled=cancelled,
    )


def test_returns_the_users_decision() -> None:
    async def scenario() -> str:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        fut.set_result("allow")
        return await _wait(fut)

    assert asyncio.run(scenario()) == "allow"


def test_an_unanswered_prompt_keeps_waiting() -> None:
    """Nobody at the keyboard is not a denial — the request stays pending."""

    async def scenario() -> bool:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        task = asyncio.ensure_future(_wait(fut))
        await asyncio.sleep(0.05)
        pending = not task.done()
        task.cancel()
        return pending

    assert asyncio.run(scenario()) is True


def test_a_late_answer_is_still_the_users_answer() -> None:
    """The user stepped away and came back; the decision must still land."""

    async def scenario() -> str:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        task = asyncio.ensure_future(_wait(fut))
        await asyncio.sleep(0.05)
        fut.set_result("allow")
        return await task

    assert asyncio.run(scenario()) == "allow"


def test_returns_the_callers_sentinel_when_the_turn_is_torn_down() -> None:
    async def scenario() -> str:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        task = asyncio.ensure_future(_wait(fut, cancelled="__cancelled__"))
        await asyncio.sleep(0)
        fut.cancel()
        return await task

    assert asyncio.run(scenario()) == "__cancelled__"
