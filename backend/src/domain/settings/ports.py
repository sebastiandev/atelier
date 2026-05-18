"""Persistence port for user settings."""

from __future__ import annotations

from typing import Protocol

from src.domain.settings.models import UserSettings


class UserSettingsRepository(Protocol):
    """SQLite-side operations on the singleton ``user_settings`` row.

    ``get`` always returns a ``UserSettings`` — never ``None`` — by
    creating the singleton row on first access if it doesn't exist yet.
    That lets callers treat the row as always-present and only worry
    about whether individual fields are populated.

    ``put`` upserts a partial: only fields that are not ``None`` on the
    incoming entity overwrite the stored row. Pass ``None`` to leave a
    field untouched, not to clear it (clearing isn't a use case the FE
    exercises today).
    """

    def get(self) -> UserSettings: ...

    def put(self, settings: UserSettings) -> UserSettings: ...
