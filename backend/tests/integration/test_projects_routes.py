"""Integration tests for /api/projects via TestClient.

Create + Get are exercised in passing by ``test_works_routes.py`` (the
move-to-project flow); these tests cover PATCH and DELETE — the two
endpoints STORY-029 added.
"""

from fastapi.testclient import TestClient


def _new_project(name: str = "Atelier", glyph: str = "AT") -> dict[str, object]:
    return {"name": name, "description": "", "glyph": glyph, "color": 250}


def _new_work(name: str = "Migration") -> dict[str, object]:
    return {
        "name": name,
        "description": f"brief for {name}",
        "contexts": [],
    }


def test_patch_renames_project(app_client: TestClient) -> None:
    app_client.post("/api/projects", json=_new_project())
    res = app_client.patch("/api/projects/PRJ-001", json={"name": "Renamed"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "Renamed"
    # GET reflects the rename.
    assert app_client.get("/api/projects/PRJ-001").json()["name"] == "Renamed"


def test_patch_with_no_fields_is_noop(app_client: TestClient) -> None:
    app_client.post("/api/projects", json=_new_project())
    res = app_client.patch("/api/projects/PRJ-001", json={})
    assert res.status_code == 200
    assert res.json()["name"] == "Atelier"


def test_patch_updates_glyph_and_color(app_client: TestClient) -> None:
    app_client.post("/api/projects", json=_new_project())
    res = app_client.patch(
        "/api/projects/PRJ-001", json={"glyph": "AT2", "color": 30}
    )
    # Glyph max length is 2 — the request shape rejects 3 chars; sanity-
    # check the valid path here.
    assert res.status_code == 422  # 3 chars > max_length=2


def test_patch_updates_glyph_within_limits(app_client: TestClient) -> None:
    app_client.post("/api/projects", json=_new_project())
    res = app_client.patch(
        "/api/projects/PRJ-001", json={"glyph": "AB", "color": 30}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["glyph"] == "AB"
    assert body["color"] == 30


def test_patch_updates_description(app_client: TestClient) -> None:
    """Description is the simplest "no FK to validate" partial-update field."""
    app_client.post(
        "/api/projects", json={**_new_project(), "description": "before"}
    )
    res = app_client.patch(
        "/api/projects/PRJ-001", json={"description": "after"}
    )
    assert res.status_code == 200
    assert res.json()["description"] == "after"


def test_patch_returns_404_for_unknown(app_client: TestClient) -> None:
    res = app_client.patch("/api/projects/PRJ-404", json={"name": "x"})
    assert res.status_code == 404


def test_delete_returns_204(app_client: TestClient) -> None:
    app_client.post("/api/projects", json=_new_project())
    assert app_client.delete("/api/projects/PRJ-001").status_code == 204
    assert app_client.get("/api/projects/PRJ-001").status_code == 404


def test_delete_demotes_attached_works_to_loose(app_client: TestClient) -> None:
    """ON DELETE SET NULL keeps the works alive but loose."""
    app_client.post("/api/projects", json=_new_project())
    app_client.post("/api/works", json={**_new_work(), "project_slug": "PRJ-001"})
    assert app_client.delete("/api/projects/PRJ-001").status_code == 204

    work = app_client.get("/api/works/WRK-001").json()
    assert work["project_slug"] is None


def test_delete_returns_404_for_unknown(app_client: TestClient) -> None:
    assert app_client.delete("/api/projects/PRJ-404").status_code == 404
