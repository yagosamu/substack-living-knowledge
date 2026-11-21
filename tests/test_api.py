from __future__ import annotations

from fastapi.testclient import TestClient

from living_kb.api import app


def test_inngest_serve_endpoint_is_mounted() -> None:
    with TestClient(app) as client:
        response = client.get("/api/inngest")
    assert response.status_code == 200
    assert response.json()["function_count"] >= 1
