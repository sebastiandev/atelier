from fastapi.testclient import TestClient

from src.main import app


def test_health_returns_ok_and_version() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert isinstance(body["version"], str)
    assert body["version"]
