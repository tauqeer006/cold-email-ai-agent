from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers.get("x-request-id")


def test_saved_contact_endpoint_is_removed(client):
    response = client.post(
        "/contacts",
        json={
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "company_name": "ExampleCo",
            "domain": "example.com",
            "position": "Marketing Lead",
            "department": "Marketing",
        },
    )
    assert response.status_code == 404


def test_start_batch(client):
    discovered = [
        {
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "company_name": "ExampleCo",
            "domain": "example.com",
            "position": "Operations Lead",
            "department": "Operations",
        }
    ]
    with (
        patch("app.routers.batches.discover_contacts_for_domain", new=AsyncMock(return_value=discovered)),
        patch("app.routers.batches.process_batch_job", new=AsyncMock()),
    ):
        response = client.post("/batches", json={"email_count": 1, "domain": "example.com"})
    assert response.status_code == 200
    payload = response.json()
    assert "batch_id" in payload
    assert payload["total_requested"] == 1
    assert payload["emails"][0]["email"] == "alice@example.com"
