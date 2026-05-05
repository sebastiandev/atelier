"""Unit tests for the Sentry fetcher.

Two endpoints are exercised: the issue summary and the latest event.
The mock transport dispatches by URL substring so each test can shape
both responses independently.
"""

from typing import Any, Callable

import httpx
import pytest

from src.domain.connections.configs import SentryConfig
from src.domain.connections.dtos import ContextFetchError
from src.domain.models import Context
from src.infrastructure.connections.fetchers import sentry as sentry_module
from src.infrastructure.connections.fetchers.sentry import fetch_sentry


def _config(org: str = "acme") -> SentryConfig:
    return SentryConfig(org=org)


def _ctx(value: str = "12345") -> Context:
    return Context(type="sentry", value=value, conn_id="con-1")


Handler = Callable[[httpx.Request], httpx.Response]


def _install(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    transport = httpx.MockTransport(handler)

    def _get(url: str, **kwargs: Any) -> httpx.Response:
        with httpx.Client(transport=transport) as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr(sentry_module.httpx, "get", _get)


def _route(
    issue: dict[str, Any] | int,
    event: dict[str, Any] | int | None = None,
) -> Handler:
    """Returns a handler that dispatches on URL — ``/events/latest/`` →
    event response, anything else → issue response. Pass an int to
    return that status code with an empty JSON body. Pass ``None`` for
    event to return 404."""

    def _resp(value: dict[str, Any] | int | None) -> httpx.Response:
        if value is None:
            return httpx.Response(404, json={"detail": "no event"})
        if isinstance(value, int):
            return httpx.Response(value, json={})
        return httpx.Response(200, json=value)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/events/latest/" in str(request.url):
            return _resp(event)
        return _resp(issue)

    return handler


# ---------------------------------------------------------------------------
# Issue-level paths (header rendering, URL normalisation, errors)


def test_success_renders_header_from_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/events/latest/" in url:
            seen["event_url"] = url
            seen["event_auth"] = request.headers.get("authorization")
            return httpx.Response(404)
        seen["issue_url"] = url
        seen["issue_auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "shortId": "PROJ-7A",
                "title": "TypeError: cannot read property",
                "level": "error",
                "status": "unresolved",
                "count": "42",
                "culprit": "app/handlers.py in handle",
                "firstSeen": "2026-04-01T09:00:00Z",
                "lastSeen": "2026-05-04T18:30:00Z",
                "permalink": "https://acme.sentry.io/issues/12345/",
            },
        )

    _install(monkeypatch, handler)
    body = fetch_sentry(_config(), _ctx(), "tok")

    assert "# PROJ-7A — TypeError: cannot read property" in body
    assert "**Level:** error" in body
    assert "**Status:** unresolved" in body
    assert "**Events:** 42" in body
    assert "**Culprit:** app/handlers.py in handle" in body
    assert "**First seen:** 2026-04-01T09:00:00Z" in body
    assert "**Last seen:** 2026-05-04T18:30:00Z" in body
    assert "**Link:** https://acme.sentry.io/issues/12345/" in body
    # Both endpoints get hit, both auth-bearer'd, both org-scoped.
    assert "organizations/acme/issues/12345/" in seen["issue_url"]
    assert "organizations/acme/issues/12345/events/latest/" in seen["event_url"]
    assert seen["issue_auth"] == "Bearer tok"
    assert seen["event_auth"] == "Bearer tok"


def test_404_on_issue_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _route(issue=404))
    with pytest.raises(ContextFetchError, match="not found"):
        fetch_sentry(_config(), _ctx(), "tok")


def test_401_on_issue_raises_auth_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _route(issue=401))
    with pytest.raises(ContextFetchError, match="auth failed"):
        fetch_sentry(_config(), _ctx(), "tok")


def test_5xx_on_issue_raises_generic_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _route(issue=503))
    with pytest.raises(ContextFetchError, match="HTTP 503"):
        fetch_sentry(_config(), _ctx(), "tok")


def test_network_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    _install(monkeypatch, boom)
    with pytest.raises(ContextFetchError, match="network error"):
        fetch_sentry(_config(), _ctx(), "tok")


def test_empty_value_raises() -> None:
    with pytest.raises(ContextFetchError, match="recognisable issue ID"):
        fetch_sentry(_config(), _ctx(""), "tok")


def test_unrecognisable_value_raises() -> None:
    with pytest.raises(ContextFetchError, match="recognisable issue ID"):
        fetch_sentry(_config(), _ctx("not-an-id"), "tok")


def test_issue_url_is_normalised_to_id(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/events/latest/" not in url:
            seen["issue_url"] = url
        return httpx.Response(
            200,
            json={"title": "x", "level": "error", "status": "unresolved", "count": "1"},
        ) if "/events/latest/" not in url else httpx.Response(404)

    _install(monkeypatch, handler)
    fetch_sentry(_config(), _ctx("https://acme.sentry.io/issues/98765/"), "tok")
    assert "organizations/acme/issues/98765/" in seen["issue_url"]


def test_url_with_query_params_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/events/latest/" not in url:
            seen["issue_url"] = url
            return httpx.Response(
                200,
                json={"title": "x", "level": "error", "status": "unresolved", "count": "1"},
            )
        return httpx.Response(404)

    _install(monkeypatch, handler)
    fetch_sentry(
        _config(),
        _ctx("https://acme.sentry.io/issues/98765/?project=42"),
        "tok",
    )
    assert "organizations/acme/issues/98765/" in seen["issue_url"]


def test_non_json_issue_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/events/latest/" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=b"<html>nope</html>")

    _install(monkeypatch, handler)
    with pytest.raises(ContextFetchError, match="non-JSON"):
        fetch_sentry(_config(), _ctx(), "tok")


# ---------------------------------------------------------------------------
# Event 404 fallback — issue header still renders


def test_event_404_falls_back_to_header_only(monkeypatch: pytest.MonkeyPatch) -> None:
    issue = {
        "title": "boom",
        "level": "error",
        "status": "unresolved",
        "count": "1",
        "metadata": {"type": "TypeError", "value": "x"},
    }
    _install(monkeypatch, _route(issue=issue, event=None))
    body = fetch_sentry(_config(), _ctx(), "tok")

    assert "# 12345 — boom" in body
    assert "latest event unavailable" in body
    # Falls back to issue.metadata for the error block.
    assert "**TypeError**" in body
    assert "## Stacktrace" not in body
    assert "## Tags" not in body


def test_event_5xx_falls_back_to_header_only(monkeypatch: pytest.MonkeyPatch) -> None:
    issue = {"title": "boom", "level": "error", "status": "unresolved", "count": "1"}
    _install(monkeypatch, _route(issue=issue, event=503))
    body = fetch_sentry(_config(), _ctx(), "tok")
    assert "latest event unavailable" in body


# ---------------------------------------------------------------------------
# Event-derived sections


_FULL_EVENT: dict[str, Any] = {
    "entries": [
        {
            "type": "exception",
            "data": {
                "values": [
                    {
                        "type": "TypeError",
                        "value": "cannot read property 'name' of undefined",
                        "module": "builtins",
                        "stacktrace": {
                            "frames": [
                                {
                                    "function": "wsgi_app",
                                    "filename": "site-packages/flask/app.py",
                                    "lineNo": 2000,
                                    "inApp": False,
                                },
                                {
                                    "function": "handle",
                                    "filename": "app/handlers.py",
                                    "lineNo": 42,
                                    "inApp": True,
                                    "context": [
                                        [40, "    data = parse_request(req)"],
                                        [41, "    if not data:"],
                                        [42, "        return data['name']"],
                                        [43, ""],
                                        [44, "def parse_request(req):"],
                                    ],
                                },
                            ]
                        },
                    }
                ]
            },
        },
        {
            "type": "request",
            "data": {
                "url": "https://api.example.com/users/42",
                "method": "POST",
                "query": [["q", "foo"]],
                "headers": [
                    ["Content-Type", "application/json"],
                    ["Authorization", "Bearer secret-xyz"],
                    ["X-Request-Id", "abc-123"],
                ],
                "data": {"user_id": 42, "name": None},
            },
        },
        {
            "type": "breadcrumbs",  # noise — should be ignored
            "data": {"values": []},
        },
    ],
    "tags": [
        {"key": "browser", "value": "Chrome 90.0"},
        {"key": "environment", "value": "prod"},
    ],
    "contexts": {
        "runtime": {"type": "runtime", "name": "python", "version": "3.11.5"},
        "os": {"type": "os", "name": "Linux", "version": "5.15"},
        "feature_flags": {"new_checkout": True},  # custom
    },
    "context": {"request_id": "abc-123", "user_id": 42},
}


def test_event_renders_error_with_module(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _route(issue={"title": "x", "level": "error"}, event=_FULL_EVENT))
    body = fetch_sentry(_config(), _ctx(), "tok")

    assert "## Error" in body
    assert "**TypeError**" in body
    assert "_(builtins)_" in body
    assert "cannot read property 'name' of undefined" in body


def test_event_renders_stacktrace_with_in_app_marker_and_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _route(issue={"title": "x", "level": "error"}, event=_FULL_EVENT))
    body = fetch_sentry(_config(), _ctx(), "tok")

    assert "## Stacktrace" in body
    # Both frames are rendered (system + in-app).
    assert "`wsgi_app` at `site-packages/flask/app.py:2000`" in body
    assert "**[in-app]** `handle` at `app/handlers.py:42`" in body
    # Source-context lines from the in-app frame appear with line numbers.
    assert "42         return data['name']" in body


def test_event_renders_request_with_redacted_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _route(issue={"title": "x", "level": "error"}, event=_FULL_EVENT))
    body = fetch_sentry(_config(), _ctx(), "tok")

    assert "## HTTP Request" in body
    assert "**POST** `https://api.example.com/users/42`" in body
    assert "`q` = `foo`" in body
    assert "`Content-Type`: `application/json`" in body
    assert "`X-Request-Id`: `abc-123`" in body
    # Authorization header value is redacted.
    assert "secret-xyz" not in body
    assert "`Authorization`: `***`" in body
    # Body is rendered as a JSON-ish block.
    assert '"user_id": 42' in body


def test_event_renders_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _route(issue={"title": "x", "level": "error"}, event=_FULL_EVENT))
    body = fetch_sentry(_config(), _ctx(), "tok")

    assert "## Tags" in body
    assert "`browser` = `Chrome 90.0`" in body
    assert "`environment` = `prod`" in body


def test_event_renders_contexts(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _route(issue={"title": "x", "level": "error"}, event=_FULL_EVENT))
    body = fetch_sentry(_config(), _ctx(), "tok")

    assert "## Contexts" in body
    # Known contexts come first in stable order.
    runtime_idx = body.index("**runtime**")
    os_idx = body.index("**os**")
    custom_idx = body.index("**feature_flags**")
    assert runtime_idx < os_idx < custom_idx
    assert "`name`: `python`" in body
    assert "`version`: `3.11.5`" in body
    # Boolean values from custom contexts get stringified.
    assert "`new_checkout`: `True`" in body


def test_event_renders_additional_data(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _route(issue={"title": "x", "level": "error"}, event=_FULL_EVENT))
    body = fetch_sentry(_config(), _ctx(), "tok")

    assert "## Additional data" in body
    assert '"request_id": "abc-123"' in body
    assert '"user_id": 42' in body


def test_event_with_only_message_omits_optional_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Message-only events (no exception, no request, no tags, etc.)
    should still render cleanly — just the header + nothing else."""
    minimal_event: dict[str, Any] = {"entries": [{"type": "message", "data": {}}]}
    _install(
        monkeypatch,
        _route(
            issue={"title": "Empty", "level": "info", "status": "unresolved", "count": "1"},
            event=minimal_event,
        ),
    )
    body = fetch_sentry(_config(), _ctx(), "tok")
    assert "# 12345 — Empty" in body
    assert "## Stacktrace" not in body
    assert "## HTTP Request" not in body
    assert "## Tags" not in body
    assert "## Contexts" not in body
    assert "## Additional data" not in body
