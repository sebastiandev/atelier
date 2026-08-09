"""Tests for pull-request loop state actions."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.artifacts.models import PrArtifact
from src.domain.loop import actions, feedback, pr_lifecycle
from src.domain.loop.dtos import (
    LoopPrConfig,
    PrStage,
)
from src.domain.loop.models import LoopRunTarget
from src.domain.models import Agent, AgentStatus


class _WorkStore:
    """Expose only PR-capture reads needed by the lifecycle action."""

    def __init__(
        self,
        *,
        artifacts: list[PrArtifact] | None = None,
        agents: list[Agent] | None = None,
    ) -> None:
        self.artifacts = artifacts or []
        self.agents = agents or []

    def list_artifacts_for_work(self, work_slug: str) -> list[PrArtifact]:
        """Return configured PR artifacts."""
        return self.artifacts

    def list_agents_for_work(self, work_slug: str) -> list[Agent]:
        """Return configured stage agents."""
        return self.agents


def _accepted_target() -> LoopRunTarget:
    stages = [
        {
            "id": "implement",
            "name": "Implement",
            "kind": "agent_task",
            "agent": {
                "session": "fresh",
                "permissions": "write",
                "provider": None,
                "model": None,
                "effort": None,
            },
            "transitions": {"pass": "approve"},
        },
        {
            "id": "approve",
            "name": "Approve",
            "kind": "user_approval",
            "agent": None,
            "transitions": {"pass": "complete"},
        },
    ]
    return LoopRunTarget(
        work_slug="WRK-001",
        run_id="run-1",
        target_id="objective",
        title="Goal",
        source_ref="Goal",
        run={
            "status": "accepted",
            "accepted_at": "2026-07-20T00:00:00+00:00",
            "loop": {
                "status": "accepted",
                "current_stage_id": "approve",
                "definition_revision": "old",
                "definition_snapshot": {
                    "id": "loop",
                    "name": "Loop",
                    "description": "",
                    "scope": "builtin",
                    "revision": "old",
                    "stages": stages,
                },
                "stages": [
                    {"id": "implement", "kind": "agent_task", "status": "passed"},
                    {"id": "approve", "kind": "user_approval", "status": "passed"},
                ],
            },
        },
    )


def test_one_off_pr_becomes_a_durable_next_stage() -> None:
    target = _accepted_target()

    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )

    loop = target.run["loop"]
    snapshot = loop["definition_snapshot"]
    assert snapshot["scope"] == "work"
    assert snapshot["stages"][-1]["kind"] == "pr"
    assert snapshot["stages"][-1]["pr_config"]["name_template"] == "feat: complete the goal"
    assert snapshot["stages"][-2]["transitions"]["pass"] == "create-pr"
    assert loop["approval_decision"] == {"summary": "Approved by user."}
    assert target.run["status"] == "running"
    assert target.run["accepted_at"] is None


def test_feedback_opens_a_new_pass_with_selected_comment_context() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    loop.pop("approval_decision")
    target.run["status"] = "accepted"
    loop["status"] = "accepted"
    loop["stages"][-1]["status"] = "passed"
    loop["pr"] = {"url": "https://github.com/acme/repo/pull/12"}
    loop["pr_comments"] = [
        {
            "id": "comment-1",
            "author": "reviewer",
            "location": "src/app.py:4",
            "body": "Handle the empty case.",
        }
    ]

    pr_lifecycle.prepare_feedback(
        target,
        (pr_lifecycle.PrFeedbackItem("comment-1", "Add a regression test."),),
        "Keep the public API unchanged.",
    )

    assert loop["pass_number"] == 2
    entry = feedback.records(loop)[0]
    assert entry["source"] == "pr_comment"
    assert entry["state"] == "open"
    assert entry["items"][0]["ref"] == "comment-1"
    assert entry["reason"] == "from PR #12 feedback"
    assert entry["created_at"]
    assert "Handle the empty case" in loop["pr_feedback_decision"]["summary"]
    assert target.run["status"] == "running"


@pytest.mark.parametrize("comment_id", ["root", "reply"])
def test_feedback_rejects_a_review_thread_last_answered_by_viewer(comment_id: str) -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    loop.pop("approval_decision")
    target.run["status"] = "accepted"
    loop["status"] = "accepted"
    loop["stages"][-1]["status"] = "passed"
    loop["pr"] = {"url": "https://github.com/acme/repo/pull/12"}
    loop["pr_comments"] = [
        {
            "id": "root",
            "author": "reviewer",
            "body": "Please handle this case.",
            "created_at": "2026-07-20T10:00:00Z",
            "kind": "review",
            "reply_target_id": "thread-1",
            "is_viewer": False,
        },
        {
            "id": "reply",
            "author": "seba",
            "body": "This is intentional.",
            "created_at": "2026-07-20T10:01:00Z",
            "kind": "review",
            "reply_target_id": "thread-1",
            "is_viewer": True,
        },
    ]

    with pytest.raises(pr_lifecycle.PrStageInvalid, match="unavailable"):
        pr_lifecycle.prepare_feedback(
            target,
            (pr_lifecycle.PrFeedbackItem(comment_id, ""),),
            "",
        )


def test_feedback_keeps_thread_context_when_latest_reply_is_external() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    loop.pop("approval_decision")
    target.run["status"] = "accepted"
    loop["status"] = "accepted"
    loop["stages"][-1]["status"] = "passed"
    loop["pr"] = {"url": "https://github.com/acme/repo/pull/12"}
    loop["pr_comments"] = [
        {
            "id": "root",
            "author": "reviewer",
            "body": "Please handle this case.",
            "created_at": "2026-07-20T10:00:00Z",
            "kind": "review",
            "reply_target_id": "thread-1",
        },
        {
            "id": "viewer-reply",
            "author": "seba",
            "body": "This is intentional.",
            "created_at": "2026-07-20T10:01:00Z",
            "kind": "review",
            "reply_target_id": "thread-1",
            "is_viewer": True,
        },
        {
            "id": "reviewer-reply",
            "author": "reviewer",
            "body": "Please document that tradeoff.",
            "created_at": "2026-07-20T10:02:00Z",
            "kind": "review",
            "reply_target_id": "thread-1",
        },
    ]

    pr_lifecycle.prepare_feedback(
        target,
        (pr_lifecycle.PrFeedbackItem("reviewer-reply", "Add a note."),),
        "",
    )

    selected = feedback.records(loop)[0]["items"][0]
    assert selected["ref"] == "reviewer-reply"
    assert selected["body"] == "Please handle this case."
    assert [row["author"] for row in selected["thread"]] == [
        "reviewer",
        "seba",
        "reviewer",
    ]
    assert "Please document that tradeoff." in loop["pr_feedback_decision"]["summary"]


def test_pr_completion_seals_feedback_pass_timing_and_reason() -> None:
    target = _accepted_target()
    target.run["started_at"] = "2026-07-20T09:00:00+00:00"
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    target.run["status"] = "accepted"
    loop["status"] = "accepted"
    loop["stages"][-1]["status"] = "passed"
    loop["pr"] = {
        "url": "https://github.com/acme/repo/pull/12",
        "number": 12,
    }
    loop["pr_comments"] = [
        {
            "id": "comment-1",
            "author": "reviewer",
            "body": "Cover the retry case.",
            "created_at": "2026-07-20T10:00:00Z",
            "kind": "review",
            "reply_target_id": "thread-1",
        }
    ]
    pr_lifecycle.prepare_feedback(
        target,
        (pr_lifecycle.PrFeedbackItem("comment-1", "Add a regression test."),),
        "Address the latest review feedback.",
    )
    feedback.records(loop)[0]  # one record carries this pass
    loop["feedback"][0]["created_at"] = "2026-07-20T10:00:00+00:00"
    loop["pass_number"] += 1  # Code review requested another implementation pass.
    stage_row = loop["stages"][-1]
    stage_row["addressed_comments"] = [{"comment_id": "old-comment"}]
    stage_row["feedback_instruction"] = "Old instruction."

    pr_lifecycle.capture_completion(
        _WorkStore(),  # type: ignore[arg-type]
        target,
        stage_row,
        (),
    )

    sealed = loop["passes"][-1]
    assert stage_row["addressed_comments"] == [
        {
            "comment_id": "comment-1",
            "author": "reviewer",
            "location": "",
            "body": "Cover the retry case.",
            "instruction": "Add a regression test.",
            "thread": [
                {
                    "author": "reviewer",
                    "body": "Cover the retry case.",
                    "created_at": "2026-07-20T10:00:00Z",
                    "is_viewer": False,
                }
            ],
        }
    ]
    assert loop["pr_comments"][0]["addressed_in_pass"] == loop["pass_number"]
    assert stage_row["feedback_instruction"] == "Address the latest review feedback."
    assert sealed["started_at"] == "2026-07-20T10:00:00+00:00"
    assert sealed["reason"] == "from PR #12 feedback"
    assert sealed["elapsed_seconds"] >= 0


def test_feedback_resets_occurrence_counters_but_keeps_reports() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    target.run["status"] = "accepted"
    loop["status"] = "accepted"
    loop["pr"] = {"url": "https://github.com/acme/repo/pull/12"}
    for row in loop["stages"]:
        row["attempt"] = 4
        row["reports"] = [{"summary": f"Previous {row['id']} report"}]
        row["recovered_attempt"] = 4
        row["connection_recovered_attempt"] = 4
        row["recovered_stale_permission_ids"] = ["permission-1"]
        row["auto_approved_permission_ids"] = ["permission-2"]
    loop["stages"][-1]["status"] = "passed"

    pr_lifecycle.prepare_feedback(target, (), "Apply the requested cleanup.")

    assert loop["attempt"] == 1
    for row in loop["stages"]:
        assert row["attempt"] == 0
        assert row["reports"] == [{"summary": f"Previous {row['id']} report"}]
        assert "recovered_attempt" not in row
        assert "connection_recovered_attempt" not in row
        assert "recovered_stale_permission_ids" not in row
        assert "auto_approved_permission_ids" not in row


def test_failed_pr_stage_can_return_to_implementation_with_an_instruction() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    loop.pop("approval_decision")
    target.run["status"] = "blocked"
    loop["status"] = "failed"
    loop["stages"][-1]["status"] = "failed"

    pr_lifecycle.prepare_feedback(
        target,
        (),
        "Fix the commit hook before creating the pull request.",
    )

    assert feedback.records(loop)[-1]["reason"] == "after Create PR failure"
    assert feedback.records(loop)[-1]["source"] == "pr_general"
    assert loop["pr_feedback_decision"]["stage_id"] == "create-pr"
    assert target.run["status"] == "running"


def test_a_create_pr_that_fails_a_feedback_pass_can_still_be_recovered() -> None:
    """Recovery must survive the failure it exists for. A request is only
    marked pushed by a successful push, so gating recovery on unpushed requests
    made the one thing that failed the only thing that could clear it."""
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(target, pr_lifecycle.PrSetup(name="feat: complete the goal"))
    loop = target.run["loop"]
    loop.pop("approval_decision")
    feedback.record(loop, source="pr_comment", note="Drop the lock.", pass_number=2)
    target.run["status"] = "blocked"
    loop["status"] = "failed"
    loop["stages"][-1]["status"] = "failed"

    pr_lifecycle.prepare_feedback(target, (), "Fix the commit hook first.")

    assert feedback.records(loop)[-1]["note"] == "Fix the commit hook first."
    assert target.run["status"] == "running"


def test_losing_the_pull_request_closes_out_the_requests_that_quoted_it() -> None:
    """Records stay as history, but one still waiting for a push must not be
    carried into a different pull request's push summary."""
    loop: dict[str, object] = {"pass_number": 3, "pr": {"url": _PR_URL}, "pr_comments": []}
    feedback.record(
        loop,
        source="pr_comment",
        pass_number=3,
        items=({"ref": "comment-1", "body": "Drop the lock."},),
    )

    pr_lifecycle.clear_for_new_pr(loop)

    assert feedback.records(loop)[0]["pushed_in_pass"] == 3
    assert "Drop the lock." not in pr_lifecycle.prompt_context({"loop": loop})


def test_existing_pr_rejects_a_different_reported_reference() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    loop["pr"] = {"url": "https://github.com/acme/repo/pull/12"}

    with pytest.raises(pr_lifecycle.PrStageInvalid, match="existing pull request"):
        pr_lifecycle.capture_completion(
            _WorkStore(),  # type: ignore[arg-type]
            target,
            loop["stages"][-1],
            ("https://github.com/acme/repo/pull/13",),
        )


def test_existing_pr_rejects_a_different_stage_artifact() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    loop["pr"] = {"url": "https://github.com/acme/repo/pull/12"}
    stage_row = loop["stages"][-1]
    stage_row["agent_slug"] = "agt-pr"
    agent = Agent(
        id=7,
        slug="agt-pr",
        work_id=1,
        name="Create PR",
        persona="developer",
        role="Create the PR",
        provider="codex",
        model="gpt-5",
        folder=Path("/tmp/repo"),
        status=AgentStatus.IDLE,
        started_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    artifact = PrArtifact(
        work_id=1,
        agent_id=7,
        title="Wrong PR",
        status="open",
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
        url="https://github.com/acme/repo/pull/13",
    )

    with pytest.raises(pr_lifecycle.PrStageInvalid, match="existing pull request"):
        pr_lifecycle.capture_completion(
            _WorkStore(artifacts=[artifact], agents=[agent]),  # type: ignore[arg-type]
            target,
            stage_row,
            (),
        )


def test_initial_pr_completion_requires_a_url_or_stage_artifact() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )

    with pytest.raises(pr_lifecycle.PrStageInvalid, match="without recording"):
        pr_lifecycle.capture_completion(
            _WorkStore(),  # type: ignore[arg-type]
            target,
            target.run["loop"]["stages"][-1],
            (),
        )


@pytest.mark.parametrize(
    ("run_status", "loop_status", "stage_status", "pending"),
    [
        ("accepted", "accepted", "passed", True),  # a pass is already scheduled
        ("running", "running", "passed", False),
        ("accepted", "accepted", "running", False),
    ],
)
def test_feedback_rejects_an_existing_or_unsettled_pass(
    run_status: str,
    loop_status: str,
    stage_status: str,
    pending: bool,
) -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )
    loop = target.run["loop"]
    target.run["status"] = run_status
    loop["status"] = loop_status
    loop["stages"][-1]["status"] = stage_status
    loop["pr"] = {"url": "https://github.com/acme/repo/pull/12"}
    if pending:
        loop["pr_feedback_decision"] = {"stage_id": "create-pr", "summary": "Already asked."}

    with pytest.raises(pr_lifecycle.PrStageInvalid):
        pr_lifecycle.prepare_feedback(target, (), "Address the review feedback.")


def test_pr_prompt_context_keeps_manual_description() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(
            name="feat: complete the goal",
            description_mode="manual",
            manual_body="## Summary\nExact body.",
        ),
    )

    prompt = pr_lifecycle.prompt_context(target.run)

    assert "description: use this exact Markdown" in prompt
    assert "## Summary\nExact body." in prompt


def test_one_off_pr_stage_keeps_selected_fast_mode() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(
            name="feat: complete the goal",
            provider="codex-acp",
            model="gpt-5.5",
            fast=True,
        ),
    )

    stages = target.run["loop"]["definition_snapshot"]["stages"]
    assert stages[-1]["agent"]["fast"] is True


def test_pr_prompt_context_blocks_detached_head_without_a_branch_name() -> None:
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(
        target,
        pr_lifecycle.PrSetup(name="feat: complete the goal"),
    )

    prompt = pr_lifecycle.prompt_context(target.run)

    assert "if HEAD is detached, return blocked_user" in prompt


def test_reusable_pr_stage_snapshots_resolved_runtime_setup() -> None:
    target = _accepted_target()
    target.run["brief"] = {"goal": "Handle empty transfers"}
    stage = PrStage(
        step_id="create-pr",
        name="Create PR",
        pr_config=LoopPrConfig(
            name_template="{work-id}: {goal}",
            base_branch="main",
        ),
    )

    pr_lifecycle.snapshot_stage_config(target, stage)

    assert target.run["loop"]["pr_config"] == {
        "name": "WRK-001: Handle empty transfers",
        "name_template": "{work-id}: {goal}",
        "description_mode": "automatic",
        "description_instructions": "",
        "manual_body": "",
        "status": "draft",
        "base_branch": "main",
        "branch_name": None,
    }


def test_reusable_pr_stage_keeps_one_off_runtime_setup() -> None:
    target = _accepted_target()
    target.run["loop"]["pr_config"] = {"name": "User-selected title"}
    stage = PrStage(
        step_id="create-pr",
        name="Create PR",
        pr_config=LoopPrConfig(name_template="{goal}"),
    )

    pr_lifecycle.snapshot_stage_config(target, stage)

    assert target.run["loop"]["pr_config"] == {"name": "User-selected title"}


def test_follow_up_run_inherits_only_stable_existing_pr_state() -> None:
    source_loop = {
        "pr": {"url": "https://github.com/acme/repo/pull/12", "number": 12},
        "pr_config": {"name": "Existing PR", "branch_name": "feat/existing"},
        "pr_comments": [{"id": "comment-1", "addressed_in_pass": None}],
        "feedback": [{"id": "fb-1", "source": "pr_general", "note": "old"}],
        "pr_feedback_decision": {"stage_id": "create-pr"},
        "passes": [{"number": 1}],
    }
    loop: dict[str, object] = {"status": "running"}

    pr_lifecycle.inherit_existing_pr(loop, source_loop)
    source_loop["pr"]["number"] = 99
    source_loop["pr_comments"][0]["id"] = "changed"

    assert loop["pr"] == {
        "url": "https://github.com/acme/repo/pull/12",
        "number": 12,
    }
    assert loop["pr_config"] == {
        "name": "Existing PR",
        "branch_name": "feat/existing",
    }
    assert loop["pr_comments"] == [{"id": "comment-1", "addressed_in_pass": None}]
    assert "feedback" not in loop
    assert "pr_feedback_decision" not in loop
    assert "passes" not in loop


def test_re_running_create_pr_still_carries_its_update_instructions() -> None:
    """Re-executing Create PR to update an existing PR must keep telling the
    agent to push to that PR and what this pass addressed. That copy is the
    PR stage's own input — separate from the implementation prompt, and it
    must survive dropping the duplicated feedback delivery."""
    target = _accepted_target()
    loop = target.run["loop"]
    loop["pr"] = {"url": "https://github.com/o/r/pull/12", "number": 12}
    loop["pr_config"] = {"name": "Migrate movement", "base_branch": "master"}
    feedback.record(
        loop,
        source="pr_comment",
        pass_number=actions.int_or_default(loop.get("pass_number"), 1),
        items=(
            {
                "ref": "comment-1",
                "author": "reviewer",
                "location": "src/app.py:4",
                "body": "Reduce the number of queries.",
                "instruction": "Keep the public API unchanged.",
            },
        ),
        note="Add focused validation.",
    )

    context = pr_lifecycle.prompt_context(target.run)

    assert "https://github.com/o/r/pull/12" in context
    assert "do not create another PR" in context
    assert "Addressed in this push:" in context
    assert "Reduce the number of queries." in context
    assert "Add focused validation." in context
    # One mention per comment, not one per delivery channel.
    assert context.count("Reduce the number of queries.") == 1


def _pr_artifact(url: str, status: str, day: int = 20) -> PrArtifact:
    return PrArtifact(
        work_id=1,
        agent_id=7,
        title="Story 01",
        status=status,  # type: ignore[arg-type]
        created_at=datetime(2026, 7, day, tzinfo=UTC),
        url=url,
    )


_PR_URL = "https://github.com/acme/repo/pull/13"


def test_a_run_without_a_pull_request_has_none_to_compare() -> None:
    assert pr_lifecycle.has_pull_request({}) is False
    assert pr_lifecycle.has_pull_request({"pr": {"url": ""}}) is False
    assert pr_lifecycle.has_pull_request({"pr": {"url": _PR_URL}}) is True


@pytest.mark.parametrize("status", ["open", "merged", "closed"])
def test_the_live_status_comes_from_the_matching_artifact_row(status: str) -> None:
    loop = {"pr": {"url": _PR_URL, "status": "open"}}

    assert pr_lifecycle.live_pr_status(loop, [_pr_artifact(_PR_URL, status)]) == status


def test_an_unrelated_pull_request_does_not_supply_a_status() -> None:
    loop = {"pr": {"url": _PR_URL, "status": "open"}}
    other = _pr_artifact("https://github.com/acme/repo/pull/99", "merged")

    assert pr_lifecycle.live_pr_status(loop, [other]) == ""


def test_the_newest_row_wins_when_a_pull_request_was_recorded_twice() -> None:
    loop = {"pr": {"url": _PR_URL, "status": "open"}}
    rows = [_pr_artifact(_PR_URL, "open", day=20), _pr_artifact(_PR_URL, "merged", day=22)]

    assert pr_lifecycle.live_pr_status(loop, rows) == "merged"


def test_adopting_the_live_status_leaves_the_rest_of_the_snapshot_alone() -> None:
    loop = {"pr": {"url": _PR_URL, "status": "open", "head_sha": "abc", "checks": {"total": 3}}}

    pr_lifecycle.adopt_live_pr_status(loop, "merged")

    assert loop["pr"] == {
        "url": _PR_URL,
        "status": "merged",
        "head_sha": "abc",
        "checks": {"total": 3},
    }


def test_adopting_a_status_onto_a_run_without_a_pull_request_does_nothing() -> None:
    loop: dict[str, object] = {}

    pr_lifecycle.adopt_live_pr_status(loop, "merged")

    assert loop == {}


def test_clearing_for_a_new_pull_request_keeps_the_reusable_setup() -> None:
    loop = {
        "pr": {"url": _PR_URL, "status": "closed"},
        "pr_comments": [{"comment_id": "1"}],
        "pr_config": {"base": "master", "status": "open"},
    }

    pr_lifecycle.clear_for_new_pr(loop)

    assert loop == {"pr_config": {"base": "master", "status": "open"}}


def test_pr_feedback_without_a_review_is_answered_by_the_approval() -> None:
    """Never by the PR stage. Binding the record to the stage that publishes is
    the coupling this spec removed: a publishing stage cannot act on a change
    request, so it could never discharge feedback it was handed itself."""
    target = _accepted_target()
    pr_lifecycle.add_one_off_stage(target, pr_lifecycle.PrSetup(name="feat: complete the goal"))
    loop = target.run["loop"]
    loop.pop("approval_decision")
    target.run["status"] = "accepted"
    loop["status"] = "accepted"
    loop["stages"][-1]["status"] = "passed"
    loop["pr"] = {"url": "https://github.com/acme/repo/pull/12"}

    pr_lifecycle.prepare_feedback(target, (), "Drop the lock.")

    assert [row["kind"] for row in loop["stages"]] == ["agent_task", "user_approval", "pr"]
    assert feedback.records(loop)[-1]["answered_by"] == "approve"
