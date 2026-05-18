"""SQLAlchemy core implementation of ``UserSettingsRepository``.

Singleton row pinned at ``id=1``. The table is tiny (five scalar columns,
all nullable) so we skip the ORM-mapping layer used by the other repos
and talk to the engine via SA Core selects/inserts — same approach as
``migrations.py`` on ``schema_version_table``. The repo's contract is:

  * ``get`` is total: it creates the row on first access if it doesn't
    exist yet, and returns a ``UserSettings`` with whatever's stored
    (NULLs surface as ``None`` so the caller / route layer can apply
    defaults).
  * ``put`` is partial: only the fields the caller set (non-``None``)
    overwrite the stored row. Passing ``None`` leaves the existing
    value alone — the FE PATCH-style flow sends just the field that
    changed, and we honour that.
"""

from __future__ import annotations

from sqlalchemy import Engine, insert, select, update

from src.domain.settings.models import UserSettings
from src.infrastructure.database.tables import user_settings_table

_SINGLETON_ID = 1


class SqlUserSettingsRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self) -> UserSettings:
        with self._engine.begin() as conn:
            row = conn.execute(
                select(
                    user_settings_table.c.editor,
                    user_settings_table.c.terminal,
                    user_settings_table.c.layout,
                    user_settings_table.c.accent_hue,
                    user_settings_table.c.theme,
                ).where(user_settings_table.c.id == _SINGLETON_ID)
            ).one_or_none()
            if row is None:
                conn.execute(insert(user_settings_table).values(id=_SINGLETON_ID))
                return UserSettings()
        return UserSettings(
            editor=row.editor,
            terminal=row.terminal,
            layout=row.layout,
            accent_hue=row.accent_hue,
            theme=row.theme,
        )

    def put(self, settings: UserSettings) -> UserSettings:
        # Build the partial first so we don't overwrite stored values
        # with ``None`` from fields the caller didn't touch.
        patch: dict[str, object] = {}
        if settings.editor is not None:
            patch["editor"] = settings.editor
        if settings.terminal is not None:
            patch["terminal"] = settings.terminal
        if settings.layout is not None:
            patch["layout"] = settings.layout
        if settings.accent_hue is not None:
            patch["accent_hue"] = settings.accent_hue
        if settings.theme is not None:
            patch["theme"] = settings.theme
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(user_settings_table.c.id).where(
                    user_settings_table.c.id == _SINGLETON_ID
                )
            ).scalar_one_or_none()
            if existing is None:
                conn.execute(
                    insert(user_settings_table).values(id=_SINGLETON_ID, **patch)
                )
            elif patch:
                conn.execute(
                    update(user_settings_table)
                    .where(user_settings_table.c.id == _SINGLETON_ID)
                    .values(**patch)
                )
        return self.get()


__all__ = ["SqlUserSettingsRepository"]
