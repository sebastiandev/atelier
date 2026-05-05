"""Jira ticket fetcher.

GET ``{base}/rest/api/3/issue/{key}`` with Basic auth (email + token).
Returns markdown — header with key/summary/status/assignee plus the
description body. Description is ADF (Atlassian Document Format), a
JSON tree; we walk it and emit reasonable markdown. Unknown nodes fall
through to a recursive walk of their ``content`` array, so even unsupported
node types don't lose nested text.

The context value can be either a bare key (``ENG-3421``) or a full
browse URL (``https://acme.atlassian.net/browse/ENG-3421``); the
fetcher normalises it before calling Jira's API. The placeholder in
the FE explicitly invites both forms.

All failures map to ``ContextFetchError`` with a one-line cause — the
route surfaces the message to the user as 422.
"""

import re
from typing import Any

import httpx

from src.domain.connections.configs import JiraConfig
from src.domain.connections.dtos import ContextFetchError
from src.domain.models import Context

_TIMEOUT_SECONDS = 12.0
# Jira issue keys are PROJECT-NUMBER. Project codes are uppercase
# letters/digits, must start with a letter; numbers are arbitrary
# positive ints. Used to pluck a key out of a browse URL or to
# sanity-check a bare value.
_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def fetch_jira(config: JiraConfig, context: Context, token: str) -> str:
    key = _normalise_key(context.value)
    if not key:
        raise ContextFetchError(
            f"jira context value isn't a recognisable issue key or URL: {context.value!r}"
        )

    base = config.url.rstrip("/")
    try:
        response = httpx.get(
            f"{base}/rest/api/3/issue/{key}",
            params={"fields": "summary,status,assignee,description,comment"},
            auth=(config.email, token),
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ContextFetchError(f"jira network error: {exc}") from exc

    if response.status_code == 404:
        raise ContextFetchError(f"jira issue not found: {key}")
    if response.status_code in (401, 403):
        raise ContextFetchError(
            f"jira auth failed for {key}: HTTP {response.status_code}"
        )
    if not response.is_success:
        raise ContextFetchError(f"jira HTTP {response.status_code} for {key}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ContextFetchError(f"jira returned non-JSON for {key}") from exc

    return _render_issue(key, payload)


def _render_issue(key: str, payload: dict[str, Any]) -> str:
    fields = payload.get("fields") or {}
    summary = fields.get("summary") or "(no summary)"
    status = (fields.get("status") or {}).get("name") or "unknown"
    assignee = (fields.get("assignee") or {}).get("displayName") or "Unassigned"

    parts = [
        f"# {key} — {summary}",
        "",
        f"- **Status:** {status}",
        f"- **Assignee:** {assignee}",
        "",
        "## Description",
        "",
    ]

    description = fields.get("description")
    if description is None:
        parts.append("_(no description)_")
    else:
        parts.append(_adf_to_markdown(description).rstrip() or "_(empty)_")

    parts.append("")
    parts.append("## Comments")
    parts.append("")
    parts.append(_render_comments(fields.get("comment")))

    return "\n".join(parts).rstrip() + "\n"


def _render_comments(comment_field: Any) -> str:
    """Jira returns comments as ``fields.comment.comments`` (flat list,
    ordered by ``created`` ascending). Jira's "reply" UI doesn't add
    structural threading on the API side — replies arrive as siblings,
    optionally with an ``@``-mention in the body. We render
    chronologically and let the LLM read the mentions."""
    items = (comment_field or {}).get("comments") or []
    if not items:
        return "_(no comments)_"

    # Defensive sort: Jira normally returns ascending by created, but
    # if a future API change re-orders we want chronological output.
    ordered = sorted(items, key=lambda c: c.get("created") or "")

    rendered = []
    for c in ordered:
        author = (c.get("author") or {}).get("displayName") or "Unknown"
        created = c.get("created") or ""
        body = _adf_to_markdown(c.get("body")).rstrip() or "_(empty)_"
        header = f"### {author} — {created}" if created else f"### {author}"
        rendered.append(f"{header}\n\n{body}")
    return "\n\n".join(rendered)


def _adf_to_markdown(node: Any) -> str:
    """Best-effort ADF → markdown. Handles the common node types Jira
    uses for issue descriptions; unknown nodes recurse into their
    ``content`` so nothing inline gets dropped silently."""
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    children = node.get("content") or []

    if node_type == "doc":
        return "\n\n".join(_adf_to_markdown(c) for c in children).strip() + "\n"
    if node_type == "paragraph":
        return "".join(_adf_to_markdown(c) for c in children)
    if node_type == "text":
        return _apply_marks(node.get("text") or "", node.get("marks") or [])
    if node_type == "hardBreak":
        return "  \n"
    if node_type == "heading":
        level = min(int(node.get("attrs", {}).get("level", 2)), 6)
        body = "".join(_adf_to_markdown(c) for c in children)
        return f"{'#' * level} {body}"
    if node_type == "bulletList":
        return "\n".join(f"- {_adf_to_markdown(c).strip()}" for c in children)
    if node_type == "orderedList":
        return "\n".join(
            f"{idx}. {_adf_to_markdown(c).strip()}"
            for idx, c in enumerate(children, start=1)
        )
    if node_type == "listItem":
        return "".join(_adf_to_markdown(c) for c in children)
    if node_type == "codeBlock":
        body = "".join(_adf_to_markdown(c) for c in children)
        lang = node.get("attrs", {}).get("language") or ""
        return f"```{lang}\n{body}\n```"
    if node_type == "blockquote":
        body = "\n".join(_adf_to_markdown(c) for c in children).splitlines()
        return "\n".join(f"> {line}" for line in body) if body else ""
    if node_type == "rule":
        return "---"
    return "".join(_adf_to_markdown(c) for c in children)


def _apply_marks(text: str, marks: list[Any]) -> str:
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        kind = mark.get("type")
        if kind == "strong":
            text = f"**{text}**"
        elif kind == "em":
            text = f"*{text}*"
        elif kind == "code":
            text = f"`{text}`"
        elif kind == "link":
            href = mark.get("attrs", {}).get("href") or ""
            text = f"[{text}]({href})" if href else text
    return text


def _normalise_key(value: str) -> str | None:
    """Pull an issue key out of a bare key or a browse URL.

    Accepts ``ENG-3421`` and any URL that contains ``ENG-3421`` as a
    standalone token (e.g. ``…/browse/ENG-3421?focusedCommentId=…``).
    Returns the key in upper-case, or None if nothing matches.
    """
    candidate = value.strip()
    if not candidate:
        return None
    match = _KEY_RE.search(candidate.upper())
    return match.group(1) if match else None


__all__ = ["fetch_jira"]
