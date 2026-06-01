"""User-settings REST router.

Singleton resource — one row per Atelier install, no slug. The FE
Settings page reads on boot and writes through on every field change.

Read: GET returns the stored row with defaults filled in for fields the
user hasn't picked yet, plus backend-owned descriptors for the selectable
tool options. The FE renders those descriptors instead of carrying its
own option catalog.

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
    # 278° = OKLCH hue of the Atelier dock icon (#5B5BD6). Match
    # ``SETTINGS_DEFAULTS.accentHue`` on the FE and the ``--accent-h``
    # token in styles.css.
    "accent_hue": 278,
    "theme": "ansi",
}


class ToolOptionRead(BaseModel):
    value: str
    label: str
    command: str
    url_template: str | None = None


EDITOR_OPTIONS: tuple[ToolOptionRead, ...] = (
    ToolOptionRead(
        value="vscode",
        label="VS Code",
        command="code .",
        url_template="vscode://file{path_uri}",
    ),
    ToolOptionRead(
        value="cursor",
        label="Cursor",
        command="cursor .",
        url_template="cursor://file{path_uri}",
    ),
    ToolOptionRead(
        value="zed",
        label="Zed",
        command="zed .",
        url_template="zed://file{path_segments}",
    ),
    ToolOptionRead(
        value="pycharm",
        label="PyCharm",
        command="charm .",
        url_template="pycharm://open?file={path_param}",
    ),
    ToolOptionRead(
        value="idea",
        label="IntelliJ IDEA",
        command="idea .",
        url_template="idea://open?file={path_param}",
    ),
    ToolOptionRead(
        value="webstorm",
        label="WebStorm",
        command="wstorm .",
        url_template="webstorm://open?file={path_param}",
    ),
    ToolOptionRead(
        value="vim",
        label="Vim (MacVim)",
        command="mvim .",
        url_template="mvim://open?url={file_uri}",
    ),
)

TERMINAL_OPTIONS: tuple[ToolOptionRead, ...] = (
    ToolOptionRead(value="system", label="System default", command="open -a Terminal"),
    ToolOptionRead(value="iterm2", label="iTerm2 (macOS)", command="open -a iTerm"),
    ToolOptionRead(value="terminator", label="Terminator (Linux)", command="terminator"),
    ToolOptionRead(
        value="gnome-terminal", label="GNOME Terminal", command="gnome-terminal"
    ),
    ToolOptionRead(value="konsole", label="Konsole (KDE)", command="konsole"),
    ToolOptionRead(value="tmux", label="tmux", command="tmux new"),
)


class UserSettingsRead(BaseModel):
    editor: str
    terminal: str
    layout: str
    accent_hue: int
    theme: str
    editor_options: list[ToolOptionRead]
    terminal_options: list[ToolOptionRead]


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
        editor_options=list(EDITOR_OPTIONS),
        terminal_options=list(TERMINAL_OPTIONS),
    )
