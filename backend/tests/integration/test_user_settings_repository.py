"""Integration tests for SqlUserSettingsRepository against a real SQLite
engine.

The repo is a singleton-row CRUD shim. The cases below pin the merge
semantics (PUT replaces only non-None fields), the auto-create on
first GET, and the round-trip of the integer accent_hue column.
"""

from __future__ import annotations

from sqlalchemy import Engine

from src.domain.settings import UserSettings
from src.infrastructure.database.user_settings_repository import (
    SqlUserSettingsRepository,
)


def test_get_creates_singleton_row_with_all_nulls(isolated_engine: Engine) -> None:
    repo = SqlUserSettingsRepository(isolated_engine)
    settings = repo.get()
    assert settings == UserSettings(
        editor=None, terminal=None, layout=None, accent_hue=None, theme=None
    )


def test_put_persists_full_settings(isolated_engine: Engine) -> None:
    repo = SqlUserSettingsRepository(isolated_engine)
    repo.put(
        UserSettings(
            editor="cursor",
            terminal="iterm2",
            layout="columns",
            accent_hue=42,
            theme="light",
        )
    )
    assert repo.get() == UserSettings(
        editor="cursor",
        terminal="iterm2",
        layout="columns",
        accent_hue=42,
        theme="light",
    )


def test_put_merges_partial_into_existing_row(isolated_engine: Engine) -> None:
    """PUT with a single field set leaves the others untouched — the FE
    sends only the field that changed, and the stored row keeps the
    rest."""
    repo = SqlUserSettingsRepository(isolated_engine)
    repo.put(
        UserSettings(
            editor="vscode",
            terminal="system",
            layout="tiles",
            accent_hue=250,
            theme="ansi",
        )
    )
    repo.put(UserSettings(terminal="iterm2"))
    assert repo.get() == UserSettings(
        editor="vscode",
        terminal="iterm2",
        layout="tiles",
        accent_hue=250,
        theme="ansi",
    )


def test_put_is_idempotent_after_repeated_calls(isolated_engine: Engine) -> None:
    repo = SqlUserSettingsRepository(isolated_engine)
    repo.put(UserSettings(editor="vscode"))
    repo.put(UserSettings(editor="vscode"))
    repo.put(UserSettings(editor="vscode"))
    assert repo.get().editor == "vscode"
