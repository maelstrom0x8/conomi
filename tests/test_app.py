from starlette.testclient import TestClient

from conomi.app import app


def test_health_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"Status": "UP"}
