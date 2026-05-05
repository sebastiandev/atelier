"""Unit tests for the Jira fetcher.

Uses httpx.MockTransport so no real network is touched. Each test stubs
the response shape Jira would actually return for a given status, then
asserts on the markdown the fetcher produces (or the ContextFetchError
it raises).
"""

from typing import Any

import httpx
import pytest

from src.domain.connections.configs import JiraConfig
from src.domain.connections.dtos import ContextFetchError
from src.domain.models import Context
from src.infrastructure.connections.fetchers import jira as jira_module
from src.infrastructure.connections.fetchers.jira import fetch_jira


def _config(
    url: str = "https://example.atlassian.net",
    email: str = "user@example.com",
) -> JiraConfig:
    return JiraConfig(url=url, email=email)


def _ctx(value: str = "ENG-3421") -> Context:
    return Context(type="jira", value=value, conn_id="con-1")


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Replace httpx.get with a function that runs through MockTransport.
    Keeps the same call surface (auth=, params=, timeout=) the fetcher uses."""
    transport = httpx.MockTransport(handler)

    def _get(url: str, **kwargs: Any) -> httpx.Response:
        with httpx.Client(transport=transport) as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr(jira_module.httpx, "get", _get)


def test_success_renders_summary_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "fields": {
                    "summary": "Login flaky",
                    "status": {"name": "In Progress"},
                    "assignee": {"displayName": "Ada"},
                    "description": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Repro on staging."}],
                            }
                        ],
                    },
                }
            },
        )

    _install_transport(monkeypatch, handler)
    body = fetch_jira(_config(), _ctx(), "tok")

    assert "# ENG-3421 — Login flaky" in body
    assert "**Status:** In Progress" in body
    assert "**Assignee:** Ada" in body
    assert "Repro on staging." in body
    assert "rest/api/3/issue/ENG-3421" in seen["url"]
    # Basic auth → "Basic <b64>"; the prefix is enough to confirm the auth pair was used.
    assert seen["auth"] is not None and seen["auth"].startswith("Basic ")


def test_404_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch, lambda _r: httpx.Response(404, json={"errorMessages": ["x"]})
    )
    with pytest.raises(ContextFetchError, match="not found"):
        fetch_jira(_config(), _ctx(), "tok")


def test_401_raises_auth_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(monkeypatch, lambda _r: httpx.Response(401))
    with pytest.raises(ContextFetchError, match="auth failed"):
        fetch_jira(_config(), _ctx(), "tok")


def test_5xx_raises_generic_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(monkeypatch, lambda _r: httpx.Response(503))
    with pytest.raises(ContextFetchError, match="HTTP 503"):
        fetch_jira(_config(), _ctx(), "tok")


def test_network_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    _install_transport(monkeypatch, boom)
    with pytest.raises(ContextFetchError, match="network error"):
        fetch_jira(_config(), _ctx(), "tok")


def test_empty_value_raises() -> None:
    with pytest.raises(ContextFetchError, match="recognisable issue key"):
        fetch_jira(_config(), _ctx(""), "tok")


def test_unrecognisable_value_raises() -> None:
    with pytest.raises(ContextFetchError, match="recognisable issue key"):
        fetch_jira(_config(), _ctx("not-a-key"), "tok")


def test_browse_url_is_normalised_to_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """User pastes a Jira browse URL — the fetcher should strip it down
    to the issue key before hitting the API. Otherwise GET .../issue/<URL>
    404s."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "fields": {
                    "summary": "x",
                    "status": {"name": "Open"},
                    "assignee": None,
                    "description": None,
                }
            },
        )

    _install_transport(monkeypatch, handler)
    fetch_jira(
        _config(),
        _ctx("https://shiphero.atlassian.net/browse/EN-41146"),
        "tok",
    )
    assert "rest/api/3/issue/EN-41146" in seen["url"]


def test_url_with_query_params_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    _install_transport(
        monkeypatch,
        lambda r: (seen.update(url=str(r.url)) or httpx.Response(  # type: ignore[func-returns-value]
            200,
            json={"fields": {"summary": "x", "status": {"name": "Open"}, "description": None}},
        )),
    )
    fetch_jira(
        _config(),
        _ctx("https://acme.atlassian.net/browse/ENG-3421?focusedCommentId=42"),
        "tok",
    )
    assert "rest/api/3/issue/ENG-3421" in seen["url"]


def test_lowercase_key_is_uppercased(monkeypatch: pytest.MonkeyPatch) -> None:
    """Be forgiving on input casing — Jira keys are canonically uppercase
    and the API matches case-sensitively."""
    seen: dict[str, Any] = {}
    _install_transport(
        monkeypatch,
        lambda r: (seen.update(url=str(r.url)) or httpx.Response(  # type: ignore[func-returns-value]
            200,
            json={"fields": {"summary": "x", "status": {"name": "Open"}, "description": None}},
        )),
    )
    fetch_jira(_config(), _ctx("eng-3421"), "tok")
    assert "rest/api/3/issue/ENG-3421" in seen["url"]


def test_non_json_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch, lambda _r: httpx.Response(200, content=b"<html>nope</html>")
    )
    with pytest.raises(ContextFetchError, match="non-JSON"):
        fetch_jira(_config(), _ctx(), "tok")


def test_no_description_renders_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={
                "fields": {
                    "summary": "Empty",
                    "status": {"name": "Open"},
                    "assignee": None,
                    "description": None,
                }
            },
        ),
    )
    body = fetch_jira(_config(), _ctx(), "tok")
    assert "_(no description)_" in body
    assert "**Assignee:** Unassigned" in body


def _comment(author: str, created: str, text: str) -> dict[str, Any]:
    return {
        "id": f"c-{created}",
        "author": {"displayName": author},
        "created": created,
        "body": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        },
    }


def test_fields_include_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The issue fetch must request the ``comment`` field — otherwise
    Jira returns the issue without comments and our renderer would always
    show '(no comments)'."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["fields"] = request.url.params.get("fields") or ""
        return httpx.Response(
            200,
            json={
                "fields": {
                    "summary": "x",
                    "status": {"name": "Open"},
                    "assignee": None,
                    "description": None,
                    "comment": {"comments": []},
                }
            },
        )

    _install_transport(monkeypatch, handler)
    fetch_jira(_config(), _ctx(), "tok")
    assert "comment" in seen["fields"].split(",")


def test_comments_rendered_in_chronological_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if Jira returns out-of-order, the renderer sorts by created
    ascending so the agent sees the conversation in time order."""
    out_of_order = [
        _comment("Beth", "2026-04-02T09:00:00.000+0000", "second"),
        _comment("Ada", "2026-04-01T09:00:00.000+0000", "first"),
        _comment("Cy", "2026-04-03T09:00:00.000+0000", "third"),
    ]
    _install_transport(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={
                "fields": {
                    "summary": "x",
                    "status": {"name": "Open"},
                    "assignee": None,
                    "description": None,
                    "comment": {"comments": out_of_order},
                }
            },
        ),
    )
    body = fetch_jira(_config(), _ctx(), "tok")

    assert "## Comments" in body
    # The three "first/second/third" markers appear in chronological order
    # in the rendered body, regardless of API ordering.
    first_idx = body.index("first")
    second_idx = body.index("second")
    third_idx = body.index("third")
    assert first_idx < second_idx < third_idx
    assert "### Ada — 2026-04-01T09:00:00.000+0000" in body


def test_no_comments_renders_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={
                "fields": {
                    "summary": "x",
                    "status": {"name": "Open"},
                    "assignee": None,
                    "description": None,
                    "comment": {"comments": []},
                }
            },
        ),
    )
    body = fetch_jira(_config(), _ctx(), "tok")
    assert "## Comments" in body
    assert "_(no comments)_" in body


def test_comment_body_renders_adf_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A comment's body is ADF, same as the description — marks (bold,
    code, links) should be carried through into markdown."""
    rich_comment = {
        "id": "c-1",
        "author": {"displayName": "Ada"},
        "created": "2026-04-01T09:00:00.000+0000",
        "body": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "fixed",
                            "marks": [{"type": "strong"}],
                        },
                        {"type": "text", "text": " in "},
                        {
                            "type": "text",
                            "text": "main",
                            "marks": [{"type": "code"}],
                        },
                    ],
                }
            ],
        },
    }
    _install_transport(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={
                "fields": {
                    "summary": "x",
                    "status": {"name": "Open"},
                    "assignee": None,
                    "description": None,
                    "comment": {"comments": [rich_comment]},
                }
            },
        ),
    )
    body = fetch_jira(_config(), _ctx(), "tok")
    assert "**fixed** in `main`" in body


def test_adf_handles_headings_lists_and_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={
                "fields": {
                    "summary": "Rich",
                    "status": {"name": "Open"},
                    "assignee": {"displayName": "X"},
                    "description": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "heading",
                                "attrs": {"level": 2},
                                "content": [{"type": "text", "text": "Steps"}],
                            },
                            {
                                "type": "bulletList",
                                "content": [
                                    {
                                        "type": "listItem",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [
                                                    {"type": "text", "text": "first"},
                                                ],
                                            }
                                        ],
                                    },
                                    {
                                        "type": "listItem",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [
                                                    {
                                                        "type": "text",
                                                        "text": "bold",
                                                        "marks": [{"type": "strong"}],
                                                    }
                                                ],
                                            }
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                }
            },
        ),
    )
    body = fetch_jira(_config(), _ctx(), "tok")
    assert "## Steps" in body
    assert "- first" in body
    assert "- **bold**" in body
