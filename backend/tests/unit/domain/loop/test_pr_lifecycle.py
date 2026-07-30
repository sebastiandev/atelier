"""Tests for pull-request loop state actions."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.artifacts.models import PrArtifact
from src.domain.loop import pr_lifecycle
from src.domain.loop.dtos import LoopPrConfig, LoopStepDefinition, LoopStepKind
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
    assert loop["pending_pr_feedback"]["comments"][0]["comment_id"] == "comment-1"
    assert loop["pending_pr_feedback"]["reason"] == "from PR #12 feedback"
    assert loop["pending_pr_feedback"]["started_at"]
    assert "Handle the empty case" in loop["pr_feedback_decision"]["summary"]
    assert target.run["status"] == "running"


def test_pending_feedback_context_restores_current_pass_input() -> None:
    target = _accepted_target()
    loop = target.run["loop"]
    loop["pass_number"] = 6
    loop["pending_pr_feedback"] = {
        "pass_number": 6,
        "comments": [
            {
                "author": "reviewer",
                "location": "src/app.py:4",
                "body": "Reduce the number of queries.",
                "instruction": "Keep the public API unchanged.",
            }
        ],
        "instruction": "Add focused validation.",
    }

    context = pr_lifecycle.pending_feedback_context(target.run)

    assert "Reduce the number of queries." in context
    assert "Keep the public API unchanged." in context
    assert "Add focused validation." in context


def test_pending_feedback_context_ignores_another_pass() -> None:
    target = _accepted_target()
    loop = target.run["loop"]
    loop["pass_number"] = 6
    loop["pending_pr_feedback"] = {
        "pass_number": 5,
        "comments": [{"body": "Stale feedback."}],
    }

    assert pr_lifecycle.pending_feedback_context(target.run) == ""


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

    selected = loop["pending_pr_feedback"]["comments"][0]
    assert selected["comment_id"] == "reviewer-reply"
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
    pending = loop["pending_pr_feedback"]
    pending["started_at"] = "2026-07-20T10:00:00+00:00"
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

    assert loop["pending_pr_feedback"]["reason"] == "after Create PR failure"
    assert loop["pr_feedback_decision"]["stage_id"] == "create-pr"
    assert target.run["status"] == "running"


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
        ("accepted", "accepted", "passed", True),
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
        loop["pending_pr_feedback"] = {"pass_number": 2}

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
    stage = LoopStepDefinition(
        step_id="create-pr",
        name="Create PR",
        kind=LoopStepKind.PR,
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
    stage = LoopStepDefinition(
        step_id="create-pr",
        name="Create PR",
        kind=LoopStepKind.PR,
        pr_config=LoopPrConfig(name_template="{goal}"),
    )

    pr_lifecycle.snapshot_stage_config(target, stage)

    assert target.run["loop"]["pr_config"] == {"name": "User-selected title"}


def test_follow_up_run_inherits_only_stable_existing_pr_state() -> None:
    source_loop = {
        "pr": {"url": "https://github.com/acme/repo/pull/12", "number": 12},
        "pr_config": {"name": "Existing PR", "branch_name": "feat/existing"},
        "pr_comments": [{"id": "comment-1", "addressed_in_pass": None}],
        "pending_pr_feedback": {"pass_number": 3},
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
    assert "pending_pr_feedback" not in loop
    assert "pr_feedback_decision" not in loop
    assert "passes" not in loop
