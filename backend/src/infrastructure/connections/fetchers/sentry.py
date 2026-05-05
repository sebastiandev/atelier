"""Sentry issue + latest-event fetcher.

Two HTTP calls:

  1. ``GET /api/0/organizations/{org}/issues/{issue_id}/`` — header info
     (title, level, status, counts, first/last seen, permalink).
  2. ``GET /api/0/organizations/{org}/issues/{issue_id}/events/latest/`` —
     the rich payload: stacktrace frames, HTTP request, tags, contexts
     (runtime/os/browser), and "additional data" (which Sentry's API
     names ``context`` — singular, top-level on the event).

If the event call fails (no recent event, or token lacks event scope),
the renderer degrades gracefully: the issue header still prints and the
event-derived sections are skipped with a one-line note. Issue-level
errors (404, auth) are fatal — the user pasted a bad ID or has the
wrong token, and bailing surfaces that immediately.

The context value can be either a numeric issue ID (``12345``) or a full
issue URL (``https://acme.sentry.io/issues/12345/``). All issue-fetch
failures map to ``ContextFetchError`` with a one-line cause; the route
surfaces it as 422.
"""

import re
from typing import Any

import httpx

from src.domain.connections.configs import SentryConfig
from src.domain.connections.dtos import ContextFetchError
from src.domain.models import Context

_TIMEOUT_SECONDS = 12.0
# Sentry issue IDs are positive integers (opaque, ~10+ digits in practice),
# either pasted bare or embedded in a /issues/<id>/ URL path segment.
_ISSUE_ID_RE = re.compile(r"/issues/(\d+)")
_BARE_ID_RE = re.compile(r"^\d+$")

# Stacktrace-rendering caps — Sentry events for deep stacks can carry
# 100+ frames. We surface in-app frames preferentially and cap the rest.
_MAX_FRAMES = 30
_MAX_IN_APP_FRAMES = 20

# Headers we redact in the rendered HTTP-request section. Auth + session
# material is the obvious set; the agent reading the context shouldn't
# see live tokens even though the connection's own token is already
# protected upstream.
_REDACTED_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "proxy-authorization",
}

# Context groups we surface as their own bullet block. Custom contexts
# (anything not in this set) get rendered under a "custom" tail section.
_KNOWN_CONTEXT_KEYS = {"runtime", "os", "browser", "device", "trace", "app"}


def fetch_sentry(config: SentryConfig, context: Context, token: str) -> str:
    issue_id = _normalise_issue_id(context.value)
    if not issue_id:
        raise ContextFetchError(
            f"sentry context value isn't a recognisable issue ID or URL: {context.value!r}"
        )

    issue = _get_issue(config.org, issue_id, token)
    event = _try_get_event(config.org, issue_id, token)
    return _render(issue_id, issue, event)


# ---------------------------------------------------------------------------
# HTTP


def _get_issue(org: str, issue_id: str, token: str) -> dict[str, Any]:
    """Issue-level payload; failures here are fatal — the user pasted
    a bad ID or has a bad token, and we want them to know that."""
    try:
        response = httpx.get(
            f"https://sentry.io/api/0/organizations/{org}/issues/{issue_id}/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ContextFetchError(f"sentry network error: {exc}") from exc

    if response.status_code == 404:
        raise ContextFetchError(f"sentry issue not found: {issue_id}")
    if response.status_code in (401, 403):
        raise ContextFetchError(
            f"sentry auth failed for {issue_id}: HTTP {response.status_code}"
        )
    if not response.is_success:
        raise ContextFetchError(f"sentry HTTP {response.status_code} for {issue_id}")

    try:
        return response.json()
    except ValueError as exc:
        raise ContextFetchError(f"sentry returned non-JSON for {issue_id}") from exc


def _try_get_event(org: str, issue_id: str, token: str) -> dict[str, Any] | None:
    """Latest-event payload. Best-effort: any failure here yields None
    so the issue header still renders. The renderer marks event-derived
    sections as unavailable rather than failing the whole context fetch."""
    try:
        response = httpx.get(
            f"https://sentry.io/api/0/organizations/{org}/issues/{issue_id}/events/latest/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return None

    if not response.is_success:
        return None
    try:
        return response.json()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Rendering


def _render(issue_id: str, issue: dict[str, Any], event: dict[str, Any] | None) -> str:
    parts: list[str] = []
    parts.extend(_render_header(issue_id, issue))

    if event is None:
        # Issue.metadata is all we have — render that as the error block
        # so the agent at least sees the exception type+value.
        fallback = _render_error_from_metadata(issue)
        if fallback:
            parts.append("")
            parts.extend(fallback)
        parts.append("")
        parts.append("_(latest event unavailable — issue has no recent events or token lacks event scope)_")
        return "\n".join(parts).rstrip() + "\n"

    error_section = _render_error(event, issue)
    if error_section:
        parts.append("")
        parts.extend(error_section)

    stacktrace_section = _render_stacktrace(event)
    if stacktrace_section:
        parts.append("")
        parts.extend(stacktrace_section)

    request_section = _render_request(event)
    if request_section:
        parts.append("")
        parts.extend(request_section)

    tags_section = _render_tags(event)
    if tags_section:
        parts.append("")
        parts.extend(tags_section)

    contexts_section = _render_contexts(event)
    if contexts_section:
        parts.append("")
        parts.extend(contexts_section)

    extra_section = _render_additional_data(event)
    if extra_section:
        parts.append("")
        parts.extend(extra_section)

    return "\n".join(parts).rstrip() + "\n"


def _render_header(issue_id: str, issue: dict[str, Any]) -> list[str]:
    short_id = issue.get("shortId") or issue_id
    title = issue.get("title") or "(no title)"
    level = issue.get("level") or "unknown"
    status = issue.get("status") or "unknown"
    count = issue.get("count") or "0"
    culprit = issue.get("culprit") or ""
    first_seen = issue.get("firstSeen") or ""
    last_seen = issue.get("lastSeen") or ""
    permalink = issue.get("permalink") or ""

    parts = [
        f"# {short_id} — {title}",
        "",
        f"- **Level:** {level}",
        f"- **Status:** {status}",
        f"- **Events:** {count}",
    ]
    if culprit:
        parts.append(f"- **Culprit:** {culprit}")
    if first_seen:
        parts.append(f"- **First seen:** {first_seen}")
    if last_seen:
        parts.append(f"- **Last seen:** {last_seen}")
    if permalink:
        parts.append(f"- **Link:** {permalink}")
    return parts


def _exception_entries(event: dict[str, Any]) -> list[dict[str, Any]]:
    entries = event.get("entries") or []
    return [
        e for e in entries if isinstance(e, dict) and e.get("type") == "exception"
    ]


def _request_entry(event: dict[str, Any]) -> dict[str, Any] | None:
    for e in event.get("entries") or []:
        if isinstance(e, dict) and e.get("type") == "request":
            return e.get("data") or {}
    return None


def _render_error(event: dict[str, Any], issue: dict[str, Any]) -> list[str]:
    """Pull the exception type+value from the event's exception entry.
    Falls back to the issue's metadata if the event has no exception
    (e.g. a message-only event)."""
    excs = _exception_entries(event)
    if excs:
        values = (excs[0].get("data") or {}).get("values") or []
        if values:
            parts = ["## Error", ""]
            for v in values:
                err_type = v.get("type") or ""
                err_value = v.get("value") or ""
                module = v.get("module") or ""
                header = f"**{err_type}**" if err_type else ""
                if module:
                    header = f"{header} _({module})_" if header else f"_({module})_"
                if header:
                    parts.append(header)
                if err_value:
                    parts.append("")
                    parts.append(err_value)
                parts.append("")
            return parts

    return _render_error_from_metadata(issue)


def _render_error_from_metadata(issue: dict[str, Any]) -> list[str]:
    metadata = issue.get("metadata") or {}
    err_type = metadata.get("type") or ""
    err_value = metadata.get("value") or ""
    if not (err_type or err_value):
        return []
    parts = ["## Error", ""]
    if err_type:
        parts.append(f"**{err_type}**")
    if err_value:
        parts.append("")
        parts.append(err_value)
    return parts


def _render_stacktrace(event: dict[str, Any]) -> list[str]:
    excs = _exception_entries(event)
    if not excs:
        return []
    values = (excs[0].get("data") or {}).get("values") or []
    if not values:
        return []

    # Sentry returns frames oldest-first (caller → callee); the deepest
    # / most recent frame is at the bottom. We keep that order so the
    # rendered trace matches the convention the LLM expects from Python
    # tracebacks.
    frames = (values[-1].get("stacktrace") or {}).get("frames") or []
    if not frames:
        return []

    in_app = [f for f in frames if isinstance(f, dict) and f.get("inApp")]
    rest = [f for f in frames if isinstance(f, dict) and not f.get("inApp")]
    in_app_kept = in_app[-_MAX_IN_APP_FRAMES:]
    remaining = max(0, _MAX_FRAMES - len(in_app_kept))
    rest_kept = rest[-remaining:] if remaining else []
    selected = sorted(
        in_app_kept + rest_kept,
        key=lambda f: frames.index(f),
    )
    omitted = len(frames) - len(selected)

    parts = ["## Stacktrace", ""]
    if omitted > 0:
        parts.append(f"_({omitted} frames omitted; showing {len(selected)} most relevant)_")
        parts.append("")

    for frame in selected:
        parts.extend(_render_frame(frame))
        parts.append("")
    return parts


def _render_frame(frame: dict[str, Any]) -> list[str]:
    fn = frame.get("function") or "<anonymous>"
    filename = frame.get("filename") or frame.get("absPath") or "<unknown>"
    line_no = frame.get("lineNo")
    in_app = bool(frame.get("inApp"))
    marker = "**[in-app]** " if in_app else ""
    location = f"{filename}:{line_no}" if line_no else filename
    header = f"{marker}`{fn}` at `{location}`"

    parts = [header]
    # Sentry frame ``context`` is [[lineNum, codeLine], ...] — a window
    # around the failing line, pre-zipped with line numbers. Render as
    # a fenced code block so indentation survives the markdown pass.
    context = frame.get("context") or []
    rendered_lines = [
        f"{ln} {code}"
        for entry in context
        if isinstance(entry, list) and len(entry) >= 2
        for ln, code in [(entry[0], entry[1])]
        if isinstance(code, str)
    ]
    if rendered_lines:
        parts.append("")
        parts.append("```")
        parts.extend(rendered_lines)
        parts.append("```")
    return parts


def _render_request(event: dict[str, Any]) -> list[str]:
    data = _request_entry(event)
    if not data:
        return []
    url = data.get("url") or ""
    method = (data.get("method") or "").upper()
    if not (url or method):
        return []

    parts = ["## HTTP Request", ""]
    if method or url:
        parts.append(f"**{method or '?'}** `{url or '?'}`")

    query = data.get("query") or []
    if query:
        parts.append("")
        parts.append("**Query:**")
        for pair in query:
            if isinstance(pair, list) and len(pair) >= 2:
                parts.append(f"- `{pair[0]}` = `{pair[1]}`")

    headers = data.get("headers") or []
    if headers:
        parts.append("")
        parts.append("**Headers:**")
        for pair in headers:
            if not (isinstance(pair, list) and len(pair) >= 2):
                continue
            name, value = pair[0], pair[1]
            if isinstance(name, str) and name.lower() in _REDACTED_HEADERS:
                value = "***"
            parts.append(f"- `{name}`: `{value}`")

    body = data.get("data")
    if body not in (None, "", {}, []):
        parts.append("")
        parts.append("**Body:**")
        parts.append("")
        parts.append("```")
        parts.append(_truncate(_stringify(body), 2000))
        parts.append("```")
    return parts


def _render_tags(event: dict[str, Any]) -> list[str]:
    tags = event.get("tags") or []
    bullets = [
        f"- `{t.get('key')}` = `{t.get('value')}`"
        for t in tags
        if isinstance(t, dict) and t.get("key") is not None
    ]
    if not bullets:
        return []
    return ["## Tags", "", *bullets]


def _render_contexts(event: dict[str, Any]) -> list[str]:
    contexts = event.get("contexts") or {}
    if not isinstance(contexts, dict) or not contexts:
        return []

    parts = ["## Contexts", ""]
    # Render known contexts first in a stable order, then any custom
    # contexts (whatever the SDK / user attached) at the tail.
    ordered_keys = [
        k for k in ("runtime", "os", "browser", "device", "app", "trace")
        if k in contexts
    ] + sorted(k for k in contexts if k not in _KNOWN_CONTEXT_KEYS)

    for key in ordered_keys:
        body = contexts.get(key)
        if not isinstance(body, dict):
            continue
        parts.append(f"**{key}**")
        for sub_key, sub_val in body.items():
            if sub_key == "type":
                continue
            parts.append(f"- `{sub_key}`: `{_stringify(sub_val)}`")
        parts.append("")
    return parts


def _render_additional_data(event: dict[str, Any]) -> list[str]:
    """Sentry calls this ``context`` (singular, top-level) on the event
    payload — what users see in the UI as 'Additional Data'."""
    extra = event.get("context")
    if not isinstance(extra, dict) or not extra:
        return []
    parts = ["## Additional data", "", "```json"]
    parts.append(_truncate(_stringify(extra), 4000))
    parts.append("```")
    return parts


# ---------------------------------------------------------------------------
# Helpers


def _normalise_issue_id(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    if _BARE_ID_RE.match(candidate):
        return candidate
    match = _ISSUE_ID_RE.search(candidate)
    return match.group(1) if match else None


def _stringify(value: Any) -> str:
    """Compact JSON-ish for dicts/lists; ``str`` everywhere else. Keeps
    the rendered context human-readable while still preserving structure
    for the LLM."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        import json
        try:
            return json.dumps(value, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…({len(text) - limit} more chars truncated)"


__all__ = ["fetch_sentry"]
