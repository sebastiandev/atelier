"""Domain model for the singleton user-preferences row."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserSettings:
    """Presentation prefs surfaced by the FE Settings page.

    Every field is optional on read so the repository can return a fresh
    row with defaults applied by the caller. The values themselves are
    opaque strings/ints — the route layer owns defaults and selectable
    tool descriptors, while this row only stores the user's selected
    values.
    """

    editor: str | None = None
    terminal: str | None = None
    layout: str | None = None
    accent_hue: int | None = None
    theme: str | None = None
