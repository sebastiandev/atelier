"""HTTP tests for the user-settings router.

The router is a singleton-resource shim over ``SqlUserSettingsRepository``
— the cases here verify the read-side default fill-in, that PUT round-
trips, and that PUT merges (omitted fields aren't reset)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_settings_returns_defaults_on_fresh_install(
    app_client: TestClient,
) -> None:
    """First boot — the singleton row doesn't exist yet. GET still
    responds with a fully populated body so the FE can render its
    form without knowing the defaults."""
    response = app_client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "editor": "vscode",
        "terminal": "system",
        "layout": "tiles",
        "accent_hue": 278,
        "theme": "ansi",
    }


def test_put_settings_round_trips(app_client: TestClient) -> None:
    response = app_client.put(
        "/api/settings",
        json={
            "editor": "cursor",
            "terminal": "iterm2",
            "layout": "columns",
            "accent_hue": 120,
            "theme": "light",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "editor": "cursor",
        "terminal": "iterm2",
        "layout": "columns",
        "accent_hue": 120,
        "theme": "light",
    }
    # Subsequent GET reflects the PUT.
    assert app_client.get("/api/settings").json() == response.json()


def test_put_settings_merges_partial(app_client: TestClient) -> None:
    """The FE sends only the field that changed — others must keep their
    stored value (or fall back to the default if never set)."""
    app_client.put(
        "/api/settings",
        json={
            "editor": "vim",
            "terminal": "iterm2",
            "layout": "columns",
            "accent_hue": 90,
            "theme": "dark",
        },
    )
    response = app_client.put("/api/settings", json={"terminal": "system"})
    assert response.status_code == 200
    assert response.json() == {
        "editor": "vim",
        "terminal": "system",
        "layout": "columns",
        "accent_hue": 90,
        "theme": "dark",
    }
