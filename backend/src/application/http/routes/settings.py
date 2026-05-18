"""User-settings REST router.

Singleton resource — one row per Atelier install, no slug. The FE
Settings page reads on boot and writes through on every field change.

Read: GET returns the stored row with defaults filled in for fields the
user hasn't picked yet, so the FE always renders a fully populated form
without having to know the defaults itself.

Write: PUT merges. Fields omitted from the body (or set to ``null``)
are left untouched; the FE sends only the field that changed. This
matches the per-field setter shape of the Zustand store on the FE.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.domain.settings import UserSettings, UserSettingsRepository

router = APIRouter()


DEFAULTS: dict[str, object] = {
    "editor": "vscode",
    "terminal": "system",
    "layout": "tiles",
    "accent_hue": 250,
    "theme": "ansi",
}


class UserSettingsRead(BaseModel):
    editor: str
    terminal: str
    layout: str
    accent_hue: int
    theme: str


class UserSettingsWrite(BaseModel):
    editor: str | None = Field(default=None)
    terminal: str | None = Field(default=None)
    layout: str | None = Field(default=None)
    accent_hue: int | None = Field(default=None)
    theme: str | None = Field(default=None)


def get_user_settings_repo(request: Request) -> UserSettingsRepository:
    return request.app.state.user_settings_repo  # type: ignore[no-any-return]


UserSettingsRepoDep = Annotated[
    UserSettingsRepository, Depends(get_user_settings_repo)
]


@router.get("/settings", response_model=UserSettingsRead)
def get_settings_endpoint(repo: UserSettingsRepoDep) -> UserSettingsRead:
    return _to_read(repo.get())


@router.put("/settings", response_model=UserSettingsRead)
def put_settings_endpoint(
    payload: UserSettingsWrite, repo: UserSettingsRepoDep
) -> UserSettingsRead:
    repo.put(
        UserSettings(
            editor=payload.editor,
            terminal=payload.terminal,
            layout=payload.layout,
            accent_hue=payload.accent_hue,
            theme=payload.theme,
        )
    )
    return _to_read(repo.get())


def _to_read(settings: UserSettings) -> UserSettingsRead:
    return UserSettingsRead(
        editor=settings.editor or str(DEFAULTS["editor"]),
        terminal=settings.terminal or str(DEFAULTS["terminal"]),
        layout=settings.layout or str(DEFAULTS["layout"]),
        accent_hue=settings.accent_hue
        if settings.accent_hue is not None
        else int(DEFAULTS["accent_hue"]),  # type: ignore[arg-type]
        theme=settings.theme or str(DEFAULTS["theme"]),
    )
