"""Domain model for the singleton user-preferences row."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserSettings:
    """Presentation prefs surfaced by the FE Settings page.

    Every field is optional on read so the repository can return a fresh
    row with defaults applied by the caller. The values themselves are
    opaque strings/ints — validation is at the route boundary, not here,
    since the legal vocabulary is owned by the FE's union types
    (``EditorChoice`` / ``TerminalChoice`` / ``Theme`` / ``CanvasLayout``).
    """

    editor: str | None = None
    terminal: str | None = None
    layout: str | None = None
    accent_hue: int | None = None
    theme: str | None = None
