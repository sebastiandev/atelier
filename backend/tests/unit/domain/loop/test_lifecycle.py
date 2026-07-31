"""Tests for shared loop lifecycle decisions."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from src.domain.loop import lifecycle
from src.domain.loop.models import LoopRunTarget


def _target(pass_destination: str) -> LoopRunTarget:
    snapshot = {
        "id": "loop",
        "name": "Loop",
        "description": "",
        "scope": "work",
        "revision": "rev-1",
        "stages": [
            {
                "id": "approve",
                "name": "Approve",
                "kind": "user_approval",
                "agent": None,
                "transitions": {"pass": pass_destination},
            },
            {
                "id": "publish",
                "name": "Publish",
                "kind": "agent_task",
                "agent": {"session": "fresh", "permissions": "write"},
                "transitions": {"pass": "complete"},
            },
        ],
    }
    return LoopRunTarget(
        work_slug="WRK-001",
        run_id="run-1",
        target_id="objective",
        title="Goal",
        source_ref="Goal",
        run={
            "status": "completed_pending_review",
            "completed_at": "2026-07-20T00:00:00+00:00",
            "loop": {
                "status": "awaiting_approval",
                "current_stage_id": "approve",
                "definition_snapshot": deepcopy(snapshot),
                "stages": [
                    {"id": "approve", "status": "pending"},
                    {"id": "publish", "status": "pending"},
                ],
            },
        },
    )


def test_accept_continues_when_approval_has_a_next_stage() -> None:
    target = _target("publish")

    assert lifecycle.accept(target) is False
    assert target.run["status"] == "running"
    assert target.run["loop"]["status"] == "needs_agent"
    assert target.run["loop"]["approval_decision"] == {"summary": "Approved by user."}


def test_accept_finishes_legacy_terminal_approval() -> None:
    target = _target("complete")

    assert lifecycle.accept(target) is True
    assert target.run["status"] == "accepted"
    assert target.run["loop"]["status"] == "accepted"


def test_cancel_marks_active_run_and_remaining_stages_cancelled() -> None:
    target = _target("publish")
    target.run["status"] = "running"
    target.run["completed_at"] = None
    target.run["loop"]["status"] = "running"
    target.run["loop"]["stages"][0]["status"] = "running"

    lifecycle.cancel(target)

    assert target.run["status"] == "needs_attention"
    assert target.run["loop"]["status"] == "cancelled"
    assert {stage["status"] for stage in target.run["loop"]["stages"]} == {"cancelled"}


def test_retry_reseeds_pending_pr_feedback(monkeypatch: Any) -> None:
    target = _target("publish")
    target.run["status"] = "blocked"
    loop = target.run["loop"]
    loop.update(
        {
            "status": "failed",
            "current_stage_id": "publish",
            "pass_number": 6,
            "pending_pr_feedback": {
                "pass_number": 6,
                "comments": [
                    {
                        "author": "reviewer",
                        "body": "Reduce the number of queries.",
                        "instruction": "Keep the public API unchanged.",
                    }
                ],
                "instruction": "Add focused validation.",
            },
        }
    )
    stage_row = loop["stages"][1]
    stage_row.update({"status": "failed", "agent_slug": "agt-old"})
    prompts: list[str] = []

    async def fake_launch(*args: Any, **kwargs: Any) -> str:
        """Return a replacement transcript without launching a provider."""
        return "agt-new"

    async def fake_send(*args: Any, **kwargs: Any) -> None:
        """Capture the retry prompt at the provider boundary."""
        prompts.append(kwargs["prompt"])

    monkeypatch.setattr(lifecycle, "_launch_retry_agent", fake_launch)
    monkeypatch.setattr(lifecycle.runtime, "send_loop_prompt", fake_send)
    monkeypatch.setattr(lifecycle.runtime, "last_transcript_seq", lambda *args, **kwargs: 0)
    workstore = SimpleNamespace(get_work_slug_for_agent=lambda slug: "WRK-001")

    asyncio.run(
        lifecycle.resume(
            target,
            workstore,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            retry_failed=True,
        )
    )

    assert len(prompts) == 1
    assert "Reduce the number of queries." in prompts[0]
    assert "Keep the public API unchanged." in prompts[0]
    assert "Add focused validation." in prompts[0]
    # The retry hint comes from lifecycle itself, so every retry path gets it
    # without the caller passing a note.
    assert "Retry the failed stage" in prompts[0]
    assert "previous attempt" in prompts[0]


def test_retry_reseeds_declared_previous_stage_report(monkeypatch: Any) -> None:
    target = _target("publish")
    target.run["status"] = "blocked"
    loop = target.run["loop"]
    loop["status"] = "failed"
    loop["current_stage_id"] = "publish"
    loop["definition_snapshot"]["stages"][1].update(
        {
            "kind": "agent_review",
            "context": [
                {
                    "kind": "previous_report",
                    "required": True,
                    "paths": [],
                    "step": "approve",
                    "ref": None,
                }
            ],
        }
    )
    loop["stages"][0].update(
        {
            "summary": "Implementation completed.",
            "validation_evidence": "84 focused tests passed.",
            "changed_files": [
                {"path": "src/app.py", "additions": 4, "deletions": 1}
            ],
        }
    )
    loop["stages"][1].update(
        {"status": "failed", "agent_slug": "agt-old", "summary": "Review failed."}
    )
    prompts: list[str] = []

    async def fake_launch(*args: Any, **kwargs: Any) -> str:
        """Return a replacement transcript without launching a provider."""
        return "agt-new"

    async def fake_send(*args: Any, **kwargs: Any) -> None:
        """Capture the retry prompt at the provider boundary."""
        prompts.append(kwargs["prompt"])

    monkeypatch.setattr(lifecycle, "_launch_retry_agent", fake_launch)
    monkeypatch.setattr(lifecycle.runtime, "send_loop_prompt", fake_send)
    monkeypatch.setattr(lifecycle.runtime, "last_transcript_seq", lambda *args, **kwargs: 0)
    workstore = SimpleNamespace(get_work_slug_for_agent=lambda slug: "WRK-001")

    asyncio.run(
        lifecycle.resume(
            target,
            workstore,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            retry_failed=True,
        )
    )

    assert len(prompts) == 1
    assert "Implementation completed." in prompts[0]
    assert "84 focused tests passed." in prompts[0]


def test_retry_accepts_inactive_stage(monkeypatch: Any) -> None:
    target = _target("publish")
    loop = target.run["loop"]
    loop["status"] = "waiting_report"
    loop["current_stage_id"] = "publish"
    loop["stages"][1].update({"status": "running", "agent_slug": "agt-old"})
    launched: list[bool] = []

    async def fake_launch(*args: Any, **kwargs: Any) -> str:
        """Return a replacement transcript without launching a provider."""
        launched.append(True)
        return "agt-new"

    async def fake_send(*args: Any, **kwargs: Any) -> None:
        """Accept the retry prompt at the provider boundary."""

    monkeypatch.setattr(lifecycle, "_launch_retry_agent", fake_launch)
    monkeypatch.setattr(lifecycle.runtime, "send_loop_prompt", fake_send)
    monkeypatch.setattr(lifecycle.runtime, "last_transcript_seq", lambda *args, **kwargs: 0)
    workstore = SimpleNamespace(get_work_slug_for_agent=lambda slug: "WRK-001")

    asyncio.run(
        lifecycle.resume(
            target,
            workstore,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            retry_failed=True,
        )
    )

    assert loop["status"] == "running"
    assert launched == [True]


def test_stop_stage_fails_only_the_running_stage() -> None:
    """The stage-level counterpart to cancel: the run stays retryable."""
    target = _target("publish")
    target.run["status"] = "running"
    target.run["completed_at"] = None
    loop = target.run["loop"]
    loop["status"] = "running"
    loop["current_stage_id"] = "publish"
    loop["stages"][0]["status"] = "passed"
    loop["stages"][1]["status"] = "running"

    lifecycle.stop_stage(target)

    assert target.run["status"] == "blocked"
    assert loop["status"] == "failed"
    assert loop["failure_kind"] == "stopped"
    assert "stopped manually" in loop["status_reason"]
    assert loop["findings"] == [loop["status_reason"]]
    # the stage that was running fails; the rest keep their history
    assert loop["stages"][1]["status"] == "failed"
    assert loop["stages"][0]["status"] == "passed"


def test_stop_stage_refuses_when_nothing_is_running() -> None:
    target = _target("publish")
    target.run["status"] = "running"
    target.run["completed_at"] = None
    loop = target.run["loop"]
    loop["status"] = "running"
    loop["current_stage_id"] = "publish"
    loop["stages"][1]["status"] = "pending"

    try:
        lifecycle.stop_stage(target)
    except lifecycle.LoopStageNotStoppable:
        pass
    else:  # pragma: no cover - the guard is the point of the test
        raise AssertionError("expected LoopStageNotStoppable")


def test_stop_stage_refuses_a_terminal_run() -> None:
    target = _target("publish")
    target.run["loop"]["status"] = "accepted"

    try:
        lifecycle.stop_stage(target)
    except lifecycle.LoopStageNotStoppable:
        pass
    else:  # pragma: no cover - the guard is the point of the test
        raise AssertionError("expected LoopStageNotStoppable")
