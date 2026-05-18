"""User presentation preferences (editor, terminal, layout, accent hue,
theme).

These are per-machine settings the FE used to keep in localStorage and
that now live in the DB so they follow the user across browsers on the
same Atelier host. See ``models.UserSettings`` for the canonical shape
and ``ports.UserSettingsRepository`` for the persistence boundary.
"""

from src.domain.settings.models import UserSettings
from src.domain.settings.ports import UserSettingsRepository

__all__ = ["UserSettings", "UserSettingsRepository"]
