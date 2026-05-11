"""Normalize provider-specific tool call shapes into Atelier's canonical
shape so the supervisor, transcript, and frontend renderer can target a
single contract per tool concept.

Each adapter calls ``canonicalize_tool`` before yielding ``ToolCall`` and
``PermissionRequest`` events. Provider quirks (Amp's ``cmd``/``edit_file``
naming vs Claude Code's ``command``/``Edit``) are absorbed here so they
never leak into ``domain/`` or the frontend.

Canonical names + keys (also documented on ``ToolCall`` in
``domain/agents/events.py``):

- ``Bash``      ``command``, optional ``cwd``, ``description``,
                ``run_in_background``, ``timeout``
- ``Edit``      ``path``, ``old_text``, ``new_text``, optional ``replace_all``
- ``MultiEdit`` ``path``, ``edits[]`` — each ``{old_text, new_text,
                replace_all?}``
- ``Read``      ``path``, optional ``line_range`` (``"1-100"`` or ``"1+"``)
- ``Write``     ``path``, ``content``
- ``Grep``      ``pattern``, optional ``path``
- ``Glob``      ``pattern``, optional ``path``

Provider name aliases:
- ``edit_file`` → ``Edit``    (Amp)
- ``finder``    → ``Grep``    (Amp)
- ``create_file`` → ``Write`` (Amp)

Tools without a canonical concept pass through unchanged — the frontend
falls back to a generic JSON view.
"""

from collections.abc import Callable
from typing import Any

# Provider tool name → canonical name.
_NAME_ALIASES: dict[str, str] = {
    "edit_file": "Edit",
    "finder": "Grep",
    "create_file": "Write",
}


def canonicalize_tool(
    name: str, raw: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Translate ``(name, raw_input)`` into the canonical
    ``(name, args)`` shape. Idempotent — already-canonical inputs pass
    through unchanged. Unknown tools pass through with their raw shape."""
    canonical_name = _NAME_ALIASES.get(name, name)
    mapper = _MAPPERS.get(canonical_name)
    if mapper is None:
        return canonical_name, dict(raw)
    return canonical_name, mapper(raw)


def _first_present(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the value of the first present key. Distinct from
    ``raw.get(a) or raw.get(b)`` because that pattern misclassifies a
    legitimate ``""`` / ``0`` / ``False`` as missing."""
    for k in keys:
        if k in raw:
            return raw[k]
    return None


def _bash(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cmd = _first_present(raw, ("command", "cmd"))
    if cmd is not None:
        out["command"] = cmd
    for key in ("cwd", "description", "run_in_background", "timeout"):
        if key in raw:
            out[key] = raw[key]
    return out


def _edit(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    path = _first_present(raw, ("path", "file_path"))
    if path is not None:
        out["path"] = path
    old = _first_present(raw, ("old_text", "old_string", "old_str"))
    out["old_text"] = old if old is not None else ""
    new = _first_present(raw, ("new_text", "new_string", "new_str"))
    out["new_text"] = new if new is not None else ""
    if "replace_all" in raw:
        out["replace_all"] = raw["replace_all"]
    return out


def _multi_edit(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    path = _first_present(raw, ("path", "file_path"))
    if path is not None:
        out["path"] = path
    edits_in = raw.get("edits", [])
    edits_out: list[dict[str, Any]] = []
    if isinstance(edits_in, list):
        for e in edits_in:
            if not isinstance(e, dict):
                continue
            old = _first_present(e, ("old_text", "old_string", "old_str"))
            new = _first_present(e, ("new_text", "new_string", "new_str"))
            edit_norm: dict[str, Any] = {
                "old_text": old if old is not None else "",
                "new_text": new if new is not None else "",
            }
            if "replace_all" in e:
                edit_norm["replace_all"] = e["replace_all"]
            edits_out.append(edit_norm)
    out["edits"] = edits_out
    return out


def _read(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    path = _first_present(raw, ("path", "file_path"))
    if path is not None:
        out["path"] = path
    if "line_range" in raw:
        out["line_range"] = raw["line_range"]
    elif "read_range" in raw:
        out["line_range"] = raw["read_range"]
    elif "offset" in raw or "limit" in raw:
        # Claude Code's offset+limit → derived "<start>-<end>" / "<start>+".
        offset = raw.get("offset")
        limit = raw.get("limit")
        if isinstance(limit, int):
            start = offset if isinstance(offset, int) else 1
            out["line_range"] = f"{start}-{start + limit - 1}"
        elif isinstance(offset, int):
            out["line_range"] = f"{offset}+"
    return out


def _write(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    path = _first_present(raw, ("path", "file_path"))
    if path is not None:
        out["path"] = path
    content = _first_present(raw, ("content", "contents"))
    if content is not None:
        out["content"] = content
    return out


def _grep(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    pattern = _first_present(raw, ("pattern", "query"))
    if pattern is not None:
        out["pattern"] = pattern
    if "path" in raw:
        out["path"] = raw["path"]
    return out


def _glob(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "pattern" in raw:
        out["pattern"] = raw["pattern"]
    if "path" in raw:
        out["path"] = raw["path"]
    return out


_MAPPERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "Bash": _bash,
    "Edit": _edit,
    "MultiEdit": _multi_edit,
    "Read": _read,
    "Write": _write,
    "Grep": _grep,
    "Glob": _glob,
}


__all__ = ["canonicalize_tool"]
