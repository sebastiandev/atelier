"""Story PR tracking (STORY-044): threads on a PR artifact, routed to its opener.

The GitHub gateway is stubbed on ``app.state.pr_status_poller``; agents run
the deterministic stub adapter, so "the agent received the message" is
observable through its transcript.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from src.domain.artifacts.pr_status import (
    FetchedPrLifecycle,
    PrCheckSummary,
    PrComment,
    PrLifecycle,
    PrRef,
)
from src.domain.workstore.dtos import RecordArtifactRequest
from src.infrastructure.database.migrations import initialize_database
from src.infrastructure.database.tables import schema_version_table

PR_URL = "https://github.com/acme/repo/pull/218"


class _Gateway:
    """One PR whose head moves when the test says so; records replies."""

    def __init__(self) -> None:
        self.head_sha = "aaaaaaa1"
        self.replies: list[tuple[str, str]] = []
        self.fetches = 0

    async def fetch(
        self, ref: PrRef, *, if_none_match: str | None = None, force: bool = False
    ) -> FetchedPrLifecycle:
        self.fetches += 1
        return FetchedPrLifecycle(
            lifecycle=PrLifecycle(
                status="open",
                checks=PrCheckSummary(state="passed", total=1, passed=1, failed=0, pending=0),
                review_state="changes_requested",
                comments=(
                    PrComment(
                        id="c1",
                        author="maria",
                        location="src/auth/fallback.ts:41",
                        body="This early return skips the login event.",
                        created_at="2026-09-10T10:00:00Z",
                        url=f"{PR_URL}#c1",
                        kind="review",
                        reply_target_id="thread-1",
                    ),
                    PrComment(
                        id="c2",
                        author="ben",
                        location=None,
                        body="TTL is hardcoded here.",
                        created_at="2026-09-10T10:05:00Z",
                        url=f"{PR_URL}#c2",
                        kind="conversation",
                        reply_target_id="pr-node",
                    ),
                ),
                title="feat(auth): password fallback",
                head_branch="st-04/fallback",
                base_branch="main",
                head_sha=self.head_sha,
                head_commit_url=f"https://github.com/acme/repo/commit/{self.head_sha}",
                body="Keeps password login available.",
                additions=214,
                deletions=38,
                changed_files=6,
            ),
            etag=f'"{self.head_sha}"',
            not_modified=False,
        )

    async def reply(self, ref: PrRef, comment: PrComment, body: str) -> PrComment:
        self.replies.append((comment.id, body))
        return PrComment(
            id=f"reply-{comment.id}-{len(self.replies)}",
            author="atelier",
            location=comment.location,
            body=body,
            created_at="2026-09-10T11:00:00Z",
            url=f"{PR_URL}#reply",
            kind=comment.kind,
            reply_target_id=comment.reply_target_id,
            is_viewer=True,
        )


class _Poller:
    def __init__(self, gateway: _Gateway) -> None:
        self._gateway = gateway

    def lifecycle_gateway(self) -> _Gateway:
        return self._gateway


@pytest.fixture
def gateway(app_client: TestClient) -> _Gateway:
    stub = _Gateway()
    app_client.app.state.pr_status_poller = _Poller(stub)  # type: ignore[attr-defined]
    return stub


def _work_with_pr(client: TestClient, tmp_workdir: str) -> tuple[str, str, str]:
    """A work, a stub agent, and a PR artifact that agent opened."""
    work = client.post("/api/works", json={"name": "Auth", "description": "x"}).json()
    agent = client.post(
        f"/api/works/{work['slug']}/agents",
        json={
            "name": "Developer",
            "persona": "developer",
            "role": "Implement",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
        },
    ).json()
    artifact = client.app.state.workstore.record_artifact(  # type: ignore[attr-defined]
        RecordArtifactRequest(
            work_slug=work["slug"],
            agent_slug=agent["slug"],
            type="pr",
            title="feat(auth): password fallback",
            status="open",
            url=PR_URL,
        )
    )
    return work["slug"], agent["slug"], artifact.slug


def _feedback(client: TestClient, work: str, art: str, mode: str = "implement") -> Any:
    return client.post(
        f"/api/works/{work}/artifacts/{art}/pr/feedback",
        json={
            "mode": mode,
            "note": "keep the guard",
            "comments": [{"comment_id": "c1", "instruction": "emit before return"}],
        },
    )


def _user_inputs(client: TestClient, agent: str) -> list[str]:
    res = client.get(f"/api/agents/{agent}/transcript?before_seq=1000000")
    assert res.status_code == 200, res.text
    events = res.json()["events"]
    return [e["text"] for e in events if e.get("type") == "user_input"]


def test_recorded_pr_remembers_its_opener(app_client: TestClient, tmp_workdir: str) -> None:
    work, agent, art = _work_with_pr(app_client, tmp_workdir)

    view = app_client.get(f"/api/works/{work}/artifacts/{art}/pr?refresh=false").json()

    assert view["opened_by"] == {
        "slug": agent,
        "name": "Developer",
        "persona": "developer",
        "present": True,
        "status": view["opened_by"]["status"],
    }
    listed = app_client.get(f"/api/works/{work}/artifacts").json()
    assert listed[0]["agent_slug"] == agent


def test_refresh_persists_lifecycle_and_threads(
    app_client: TestClient, tmp_workdir: str, gateway: _Gateway
) -> None:
    work, _agent, art = _work_with_pr(app_client, tmp_workdir)

    view = app_client.get(f"/api/works/{work}/artifacts/{art}/pr").json()

    assert view["pr"]["number"] == 218
    assert view["pr"]["branch"] == "st-04/fallback"
    assert view["pr"]["additions"] == 214
    assert [c["id"] for c in view["comments"]] == ["c1", "c2"]
    again = app_client.get(f"/api/works/{work}/artifacts/{art}/pr?refresh=false").json()
    assert again["pr"]["head_sha"] == "aaaaaaa1"
    assert gateway.fetches == 1


def test_feedback_goes_to_the_opener(
    app_client: TestClient, tmp_workdir: str, gateway: _Gateway
) -> None:
    work, agent, art = _work_with_pr(app_client, tmp_workdir)
    app_client.get(f"/api/works/{work}/artifacts/{art}/pr")

    res = _feedback(app_client, work, art)

    assert res.status_code == 202, res.text
    body = res.json()
    assert body["target_slug"] == agent
    assert body["relaunched"] is False
    sent = _user_inputs(app_client, agent)
    assert any(
        "This early return skips the login event." in t and "keep the guard" in t for t in sent
    )
    batch = body["view"]["feedback"][-1]
    assert batch["mode"] == "implement"
    assert batch["head_sha_at_send"] == "aaaaaaa1"
    assert batch["replied_at"] is None
    assert (
        next(c for c in body["view"]["comments"] if c["id"] == "c1")["feedback_mode"] == "implement"
    )


def test_feedback_relaunches_a_removed_opener(
    app_client: TestClient, tmp_workdir: str, gateway: _Gateway
) -> None:
    work, agent, art = _work_with_pr(app_client, tmp_workdir)
    app_client.get(f"/api/works/{work}/artifacts/{art}/pr")
    assert app_client.delete(f"/api/agents/{agent}").status_code == 204
    gone = app_client.get(f"/api/works/{work}/artifacts/{art}/pr?refresh=false").json()
    assert gone["opened_by"] == {
        "slug": None,
        "name": "Developer",
        "persona": "developer",
        "present": False,
        "status": None,
    }

    res = _feedback(app_client, work, art)

    assert res.status_code == 202, res.text
    body = res.json()
    assert body["relaunched"] is True
    # SQLite may hand the replacement the freed id, so the slug can repeat;
    # the row is new either way.
    new_slug = body["target_slug"]
    replacement = next(
        a for a in app_client.get(f"/api/works/{work}/agents").json() if a["slug"] == new_slug
    )
    assert (replacement["name"], replacement["persona"], replacement["model"]) == (
        "Developer",
        "developer",
        "smart",
    )
    assert body["view"]["opened_by"]["slug"] == new_slug
    assert body["view"]["opened_by"]["present"] is True
    assert _user_inputs(app_client, new_slug)


def test_refresh_replies_once_after_the_push_lands(
    app_client: TestClient, tmp_workdir: str, gateway: _Gateway
) -> None:
    work, _agent, art = _work_with_pr(app_client, tmp_workdir)
    app_client.get(f"/api/works/{work}/artifacts/{art}/pr")
    assert _feedback(app_client, work, art).status_code == 202
    app_client.get(f"/api/works/{work}/artifacts/{art}/pr")
    assert gateway.replies == []

    gateway.head_sha = "bbbbbbb2"
    view = app_client.get(f"/api/works/{work}/artifacts/{art}/pr").json()
    app_client.get(f"/api/works/{work}/artifacts/{art}/pr")

    assert [(cid, "bbbbbbb" in body) for cid, body in gateway.replies] == [("c1", True)]
    assert "emit before return" in gateway.replies[0][1]
    assert view["feedback"][-1]["replied_at"]
    c1 = next(c for c in view["comments"] if c["id"] == "c1")
    assert c1["reply_posted_at"]
    assert any(c.get("atelier_reply") for c in view["comments"])


def test_discuss_feedback_never_replies_on_github(
    app_client: TestClient, tmp_workdir: str, gateway: _Gateway
) -> None:
    work, _agent, art = _work_with_pr(app_client, tmp_workdir)
    app_client.get(f"/api/works/{work}/artifacts/{art}/pr")
    res = _feedback(app_client, work, art, mode="discuss")
    assert res.status_code == 202, res.text
    assert res.json()["view"]["feedback"][-1]["replied_at"]

    gateway.head_sha = "ccccccc3"
    app_client.get(f"/api/works/{work}/artifacts/{art}/pr")

    assert gateway.replies == []


@pytest.mark.parametrize("comments", [[], [{"comment_id": "nope", "instruction": ""}]])
def test_feedback_requires_a_known_comment(
    app_client: TestClient, tmp_workdir: str, gateway: _Gateway, comments: list[dict[str, str]]
) -> None:
    work, _agent, art = _work_with_pr(app_client, tmp_workdir)
    app_client.get(f"/api/works/{work}/artifacts/{art}/pr")

    res = app_client.post(
        f"/api/works/{work}/artifacts/{art}/pr/feedback",
        json={"mode": "implement", "comments": comments},
    )

    assert res.status_code == 422, res.text


def test_pr_view_404_for_unknown_artifact(app_client: TestClient, tmp_workdir: str) -> None:
    work, _agent, _art = _work_with_pr(app_client, tmp_workdir)

    res = app_client.get(f"/api/works/{work}/artifacts/art-999/pr?refresh=false")

    assert res.status_code == 404


def test_v27_upgrade_adds_nullable_artifact_lifecycle(isolated_engine: Engine) -> None:
    with isolated_engine.begin() as conn:
        conn.execute(text("ALTER TABLE artifacts DROP COLUMN lifecycle"))
        conn.execute(schema_version_table.update().values(version=26))

    initialize_database(isolated_engine)

    columns = {column["name"] for column in inspect(isolated_engine).get_columns("artifacts")}
    assert "lifecycle" in columns
