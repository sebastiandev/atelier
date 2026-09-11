"""GitHub GraphQL adapter for pull-request lifecycle and comment replies."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx

from src.domain.artifacts.models import PrStatus
from src.domain.artifacts.pr_status import (
    FetchedPrLifecycle,
    PrCheckState,
    PrCheckSummary,
    PrComment,
    PrCommentKind,
    PrLifecycle,
    PrRef,
    PrReviewState,
)
from src.infrastructure.artifacts.github_pr_status import (
    TokenSupplier,
    _gh_auth_token,
)

_log = logging.getLogger(__name__)
_GRAPHQL_URL = "https://api.github.com/graphql"
_TIMEOUT_SECONDS = 10.0
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Atelier-PR-Lifecycle",
}

_LIFECYCLE_QUERY = """
query PullRequestLifecycle($owner: String!, $repo: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id title headRefName baseRefName state isDraft mergedAt reviewDecision
      body additions deletions changedFiles
      comments(first: 100) {
        nodes { id author { login } body createdAt url }
      }
      reviewThreads(first: 100) {
        nodes {
          id
          comments(first: 100) {
            nodes { id author { login } body createdAt url path line originalLine }
          }
        }
      }
      commits(last: 1) {
        nodes {
          commit {
            oid
            url
            statusCheckRollup {
              state
              contexts(first: 100) {
                totalCount
                nodes {
                  __typename
                  ... on CheckRun { status conclusion }
                  ... on StatusContext { state }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

_REVIEW_REPLY_MUTATION = """
mutation ReplyToReview($target: ID!, $body: String!) {
  addPullRequestReviewThreadReply(
    input: {pullRequestReviewThreadId: $target, body: $body}
  ) {
    comment { id author { login } body createdAt url path line originalLine }
  }
}
"""

_CONVERSATION_REPLY_MUTATION = """
mutation ReplyToConversation($target: ID!, $body: String!) {
  addComment(input: {subjectId: $target, body: $body}) {
    commentEdge { node { id author { login } body createdAt url } }
  }
}
"""

_PASSED_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAILED_CHECKS = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "ERROR",
    "FAILURE",
    "STALE",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}


class GitHubPrLifecycleGateway:
    """Read/reply gateway backed by one shared ``httpx.AsyncClient``."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        token_supplier: TokenSupplier | None = None,
    ) -> None:
        self._client = client
        self._token_supplier = token_supplier or _gh_auth_token

    async def fetch(
        self,
        ref: PrRef,
        *,
        if_none_match: str | None = None,
        force: bool = False,
    ) -> FetchedPrLifecycle | None:
        """Fetch a lifecycle snapshot; ``force`` bypasses conditional cache."""
        response = await self._request(
            _LIFECYCLE_QUERY,
            {"owner": ref.owner, "repo": ref.repo, "number": ref.number},
            if_none_match=None if force else if_none_match,
        )
        if response is None:
            return None
        if response.status_code == 304:
            return FetchedPrLifecycle(None, if_none_match, True)
        payload = _response_payload(response)
        if payload is None:
            return None
        pr = ((payload.get("data") or {}).get("repository") or {}).get("pullRequest")
        if not isinstance(pr, dict):
            return None
        viewer = ((payload.get("data") or {}).get("viewer") or {}).get("login")
        lifecycle = _parse_lifecycle(
            cast(dict[str, Any], pr),
            viewer_login=str(viewer) if viewer else None,
        )
        if lifecycle is None:
            return None
        return FetchedPrLifecycle(
            lifecycle=lifecycle,
            etag=response.headers.get("ETag"),
            not_modified=False,
        )

    async def reply(self, ref: PrRef, comment: PrComment, body: str) -> PrComment | None:
        """Reply in the selected review thread or PR conversation."""
        is_review = comment.kind == "review"
        mutation = _REVIEW_REPLY_MUTATION if is_review else _CONVERSATION_REPLY_MUTATION
        response = await self._request(
            mutation,
            {"target": comment.reply_target_id, "body": body},
        )
        if response is None:
            return None
        payload = _response_payload(response)
        if payload is None:
            return None
        data = payload.get("data") or {}
        if is_review:
            node = (data.get("addPullRequestReviewThreadReply") or {}).get("comment")
        else:
            node = ((data.get("addComment") or {}).get("commentEdge") or {}).get("node")
        if not isinstance(node, dict):
            return None
        return _parse_comment(
            cast(dict[str, Any], node),
            kind=comment.kind,
            reply_target_id=comment.reply_target_id,
            is_viewer=True,
        )

    async def _request(
        self,
        query: str,
        variables: dict[str, object],
        *,
        if_none_match: str | None = None,
    ) -> httpx.Response | None:
        token = self._token_supplier()
        if token is None:
            return None
        headers = {**_HEADERS, "Authorization": f"Bearer {token}"}
        if if_none_match:
            headers["If-None-Match"] = if_none_match
        try:
            response = await self._client.post(
                _GRAPHQL_URL,
                headers=headers,
                json={"query": query, "variables": variables},
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            _log.warning("GitHub lifecycle request failed: %s", exc)
            return None
        if response.status_code == 304:
            return response
        if response.status_code >= 400:
            _log.warning(
                "GitHub lifecycle request returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return None
        return response


def _response_payload(response: httpx.Response) -> dict[str, Any] | None:
    """Return a GraphQL payload only when transport and query both succeeded."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    return cast(dict[str, Any], payload)


def _parse_lifecycle(pr: dict[str, Any], *, viewer_login: str | None = None) -> PrLifecycle | None:
    """Translate the GitHub GraphQL PR shape into the domain snapshot."""
    status = _map_status(pr)
    if status is None:
        return None
    pr_id = pr.get("id")
    if not isinstance(pr_id, str):
        return None
    comments: list[PrComment] = []
    for node in (pr.get("comments") or {}).get("nodes") or []:
        if isinstance(node, dict):
            parsed = _parse_comment(
                node,
                kind="conversation",
                reply_target_id=pr_id,
                viewer_login=viewer_login,
            )
            if parsed is not None:
                comments.append(parsed)
    for thread in (pr.get("reviewThreads") or {}).get("nodes") or []:
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            continue
        for node in (thread.get("comments") or {}).get("nodes") or []:
            if isinstance(node, dict):
                parsed = _parse_comment(
                    node,
                    kind="review",
                    reply_target_id=thread["id"],
                    viewer_login=viewer_login,
                )
                if parsed is not None:
                    comments.append(parsed)
    comments.sort(key=lambda item: item.created_at)
    return PrLifecycle(
        status=status,
        checks=_parse_checks(pr),
        review_state=_map_review_state(pr.get("reviewDecision")),
        comments=tuple(comments),
        title=str(pr.get("title") or ""),
        head_branch=str(pr.get("headRefName") or ""),
        base_branch=str(pr.get("baseRefName") or ""),
        head_sha=_head_commit(pr).get("oid", ""),
        head_commit_url=_head_commit(pr).get("url", ""),
        body=str(pr.get("body") or ""),
        additions=_int(pr.get("additions")),
        deletions=_int(pr.get("deletions")),
        changed_files=_int(pr.get("changedFiles")),
    )


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _head_commit(pr: dict[str, Any]) -> dict[str, str]:
    """Return the tip commit of the PR branch, or blanks when absent."""
    nodes = (pr.get("commits") or {}).get("nodes") or []
    node = nodes[0] if nodes and isinstance(nodes[0], dict) else {}
    raw = node.get("commit")
    commit: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return {
        "oid": str(commit.get("oid") or ""),
        "url": str(commit.get("url") or ""),
    }


def _map_status(pr: dict[str, Any]) -> PrStatus | None:
    """Map GitHub GraphQL state/draft fields to Atelier PR statuses."""
    state = pr.get("state")
    if state == "MERGED" or pr.get("mergedAt"):
        return "merged"
    if state == "CLOSED":
        return "closed"
    if state == "OPEN":
        return "draft" if pr.get("isDraft") else "open"
    return None


def _parse_checks(pr: dict[str, Any]) -> PrCheckSummary:
    """Aggregate GitHub check runs and status contexts into stable counts."""
    commits = (pr.get("commits") or {}).get("nodes") or []
    rollup = (
        ((commits[-1].get("commit") or {}).get("statusCheckRollup"))
        if commits and isinstance(commits[-1], dict)
        else None
    )
    contexts = (rollup or {}).get("contexts") or {}
    nodes = contexts.get("nodes") or []
    passed = failed = pending = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        value = (
            node.get("conclusion") if node.get("__typename") == "CheckRun" else node.get("state")
        )
        if value in _PASSED_CHECKS:
            passed += 1
        elif value in _FAILED_CHECKS:
            failed += 1
        else:
            pending += 1
    total = max(int(contexts.get("totalCount") or 0), passed + failed + pending)
    pending += total - passed - failed - pending
    state: PrCheckState
    if total == 0:
        state = "none"
    elif failed:
        state = "failed"
    elif pending:
        state = "pending"
    else:
        state = "passed"
    return PrCheckSummary(state, total, passed, failed, pending)


def _map_review_state(value: object) -> PrReviewState:
    """Map GitHub's nullable review decision onto the UI vocabulary."""
    if value == "APPROVED":
        return "approved"
    if value == "CHANGES_REQUESTED":
        return "changes_requested"
    if value == "REVIEW_REQUIRED":
        return "pending"
    return "none"


def _parse_comment(
    node: dict[str, Any],
    *,
    kind: PrCommentKind,
    reply_target_id: str,
    viewer_login: str | None = None,
    is_viewer: bool = False,
) -> PrComment | None:
    """Parse one conversation or review comment, preserving timestamps."""
    comment_id = node.get("id")
    created_at = node.get("createdAt")
    if not isinstance(comment_id, str) or not isinstance(created_at, str):
        return None
    author = (node.get("author") or {}).get("login") or "ghost"
    path = node.get("path")
    line = node.get("line") or node.get("originalLine")
    location = f"{path}:{line}" if path and line else str(path) if path else None
    return PrComment(
        id=comment_id,
        author=str(author),
        location=location,
        body=str(node.get("body") or ""),
        created_at=created_at,
        url=str(node.get("url") or ""),
        kind=kind,
        reply_target_id=reply_target_id,
        is_viewer=is_viewer
        or bool(viewer_login and str(author).casefold() == viewer_login.casefold()),
    )


__all__ = ["GitHubPrLifecycleGateway"]
