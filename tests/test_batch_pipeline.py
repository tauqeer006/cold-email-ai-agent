import asyncio
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import batch_service, ollama_service
from app.services.hunter_service import discover_companies, discover_contacts_for_domain
from app.services.ollama_service import generate_email_draft
from app.storage import create_batch, get_batch, get_batch_rows


def test_batch_calls_hunter_for_requested_email_count(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    discovered = [
        {
            "first_name": "Ari",
            "last_name": "Example",
            "email": "ari@example.test",
            "company_name": "Example Co",
            "domain": "example.test",
            "position": "Founder",
            "department": "Executive",
            "company_description": "A software company.",
            "source": "hunter",
            "confidence": 90,
        }
    ]
    with TestClient(app) as client, patch(
        "app.routers.batches.discover_contacts_for_domain",
        new=AsyncMock(return_value=discovered),
    ) as hunter_discovery, patch("app.routers.batches.process_batch_job", new=AsyncMock()):
        response = client.post("/batches", json={"domain": "example.test", "email_count": 1})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_requested"] == 1
        assert payload["emails"][0]["email"] == "ari@example.test"
        assert payload["emails"][0]["company_description"] == "A software company."
        hunter_discovery.assert_awaited_once_with("example.test", limit=1)

        status_response = client.get(f"/batches/{payload['batch_id']}")
        assert status_response.status_code == 200
        item = status_response.json()["items"][0]
        assert item["recipient"]["company_name"] == "Example Co"
        assert client.get("/batches").json()[0]["batch_id"] == payload["batch_id"]
        assert not (tmp_path / "contacts.csv").exists()
        assert (tmp_path / "batches.csv").exists()

        export_response = client.get(f"/batches/{payload['batch_id']}/export/csv")
        assert export_response.status_code == 200
        assert "ari@example.test" in export_response.text
        assert "company_description" in export_response.text


def test_batch_requires_a_domain_and_does_not_use_saved_contacts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    with TestClient(app) as client, patch(
        "app.routers.batches.discover_contacts_for_domain",
        new=AsyncMock(),
    ) as hunter_discovery:
        response = client.post("/batches", json={"email_count": 1})
    assert response.status_code == 422
    hunter_discovery.assert_not_awaited()


def test_get_email_preview_saves_new_company_details_and_draft_without_sending(
    tmp_path,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    discovered = [
        {
            "first_name": "Ari",
            "last_name": "Example",
            "email": "ari@example.test",
            "company_name": "Example Co",
            "domain": "example.test",
            "position": "Founder",
            "department": "Executive",
            "company_description": "A software company.",
            "source": "hunter",
            "confidence": 90,
        }
    ]
    draft = {
        "subject": "Hello Ari",
        "body": "A short, personalized email.",
        "personalization_reason": "Relevant company context.",
    }
    with (
        caplog.at_level(logging.DEBUG, logger="cold_email.batches"),
        TestClient(app) as client,
        patch(
            "app.routers.batches.discover_contacts_for_domain",
            new=AsyncMock(return_value=discovered),
        ) as hunter_discovery,
        patch(
            "app.routers.batches.generate_email_draft",
            new=AsyncMock(return_value=draft),
        ) as ollama_draft,
        patch("app.services.batch_service.send_email_via_smtp") as smtp_send,
    ):
        response = client.get("/api/emails/preview?domain=example.test&email_count=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_count"] == 1
    assert payload["skipped_existing_count"] == 0
    assert payload["batch_id"]
    assert payload["emails"][0]["company_name"] == "Example Co"
    assert payload["emails"][0]["company_description"] == "A software company."
    assert payload["emails"][0]["subject"] == "Hello Ari"
    assert payload["emails"][0]["body"] == "A short, personalized email."
    assert payload["emails"][0]["batch_id"] == payload["batch_id"]
    saved_batch = get_batch(payload["batch_id"])
    assert saved_batch["status"] == "previewed"
    assert saved_batch["items"][0]["status"] == "drafted"
    assert saved_batch["items"][0]["email_body"] == "A short, personalized email."
    request_id = response.headers["x-request-id"]
    assert request_id in caplog.text
    assert "Email preview Hunter lookup completed" in caplog.text
    assert "Email preview Ollama draft completed" in caplog.text
    assert "Email preview completed" in caplog.text
    hunter_discovery.assert_awaited_once_with("example.test", limit=1)
    ollama_draft.assert_awaited_once()
    smtp_send.assert_not_called()
    assert (tmp_path / "batches.csv").exists()


def test_email_preview_skips_addresses_already_in_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    create_batch(
        "previous-preview",
        [{"email": "ari@example.test"}],
        batch_status="previewed",
        item_status="drafted",
    )
    discovered = [
        {"email": "ARI@example.test", "company_name": "Already Contacted"},
        {"email": "new@example.test", "company_name": "New Company", "domain": "new.example.test"},
    ]
    with (
        TestClient(app) as client,
        patch(
            "app.routers.batches.discover_contacts_for_domain",
            new=AsyncMock(return_value=discovered),
        ),
        patch(
            "app.routers.batches.generate_email_draft",
            new=AsyncMock(
                return_value={
                    "subject": "Hello",
                    "body": "A new draft.",
                    "personalization_reason": "Company context.",
                }
            ),
        ) as ollama_draft,
    ):
        response = client.get("/api/emails/preview?domain=example.test&email_count=2")
        repeated_response = client.get("/api/emails/preview?domain=example.test&email_count=2")

    assert response.status_code == 200
    assert response.json()["result_count"] == 1
    assert response.json()["skipped_existing_count"] == 1
    assert [email["email"] for email in response.json()["emails"]] == ["new@example.test"]
    assert repeated_response.status_code == 200
    assert repeated_response.json()["result_count"] == 0
    assert repeated_response.json()["skipped_existing_count"] == 2
    assert "already in batches.csv" in repeated_response.json()["message"]
    assert ollama_draft.await_count == 1
    assert len(get_batch_rows(response.json()["batch_id"])) == 1


def test_send_preview_email_uses_configured_smtp_account(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    saved = create_batch(
        "preview-batch",
        [{
            "email": "recipient@example.test",
            "subject": "Hello",
            "body": "A short, personalized email.",
            "personalization_reason": "Relevant company context.",
        }],
        batch_status="previewed",
        item_status="drafted",
    )
    payload = {
        "batch_id": "preview-batch",
        "item_id": saved[0]["item_id"],
    }
    with TestClient(app) as client, patch(
        "app.routers.batches.send_email_via_smtp",
        return_value={"status": "sent", "message": "Email sent successfully."},
    ) as smtp_send:
        response = client.post("/api/emails/send", json=payload)
        repeated_response = client.post("/api/emails/send", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "status": "sent",
        "message": "Email sent from the configured SMTP account.",
    }
    smtp_send.assert_called_once_with(
        "recipient@example.test",
        "Hello",
        "A short, personalized email.",
    )
    assert repeated_response.status_code == 409
    saved_item = get_batch_rows("preview-batch")[0]
    assert saved_item["status"] == "sent"
    assert saved_item["sent"] is True


def test_send_preview_email_reports_smtp_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    saved = create_batch(
        "preview-batch",
        [{"email": "recipient@example.test", "subject": "Hello", "body": "A short draft."}],
        batch_status="previewed",
        item_status="drafted",
    )
    with TestClient(app) as client, patch(
        "app.routers.batches.send_email_via_smtp",
        return_value={"status": "error", "message": "SMTP credentials are not configured."},
    ):
        response = client.post(
            "/api/emails/send",
            json={"batch_id": "preview-batch", "item_id": saved[0]["item_id"]},
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "SMTP credentials are not configured."
    assert get_batch_rows("preview-batch")[0]["status"] == "send_failed"


def test_batch_start_skips_previously_used_recipients(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    create_batch(
        "previous-preview",
        [{"email": "used@example.test"}],
        batch_status="previewed",
        item_status="drafted",
    )
    discovered = [
        {"email": "used@example.test", "company_name": "Already Contacted"},
        {"email": "new@example.test", "company_name": "New Company"},
    ]
    with (
        TestClient(app) as client,
        patch(
            "app.routers.batches.discover_contacts_for_domain",
            new=AsyncMock(return_value=discovered),
        ),
        patch("app.routers.batches.process_batch_job", new=AsyncMock()),
    ):
        response = client.post("/batches", json={"domain": "example.test", "email_count": 2})

    assert response.status_code == 200
    assert response.json()["total_requested"] == 1
    assert [email["email"] for email in response.json()["emails"]] == ["new@example.test"]


def test_batch_rejects_empty_discovery_and_contacts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    with TestClient(app) as client, patch(
        "app.routers.batches.discover_contacts_for_domain",
        new=AsyncMock(return_value=[]),
    ):
        response = client.post("/batches", json={"domain": "empty.example.test", "email_count": 10})
    assert response.status_code == 400
    assert "Hunter found no email addresses" in response.json()["detail"]


def test_batch_pipeline_sends_generated_email_and_persists_result(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    recipient = {
        "email": "recipient@example.test",
        "company_name": "Example Co",
        "source": "hunter",
    }
    create_batch("test-batch", [recipient])
    item_id = get_batch_rows("test-batch")[0]["item_id"]

    with (
        patch.object(batch_service, "verify_email", new=AsyncMock(return_value={"status": "valid", "score": 99})),
        patch.object(
            batch_service,
            "generate_email_draft",
            new=AsyncMock(
                return_value={
                    "subject": "Hello",
                    "body": "A short, personalized message.",
                    "personalization_reason": "Relevant company context.",
                }
            ),
        ),
        patch.object(
            batch_service,
            "send_email_via_smtp",
            Mock(return_value={"status": "sent", "message": "sent"}),
        ) as send_email,
    ):
        with caplog.at_level(logging.DEBUG, logger="batch_service"):
            asyncio.run(batch_service.process_batch_job("test-batch"))

    batch = get_batch("test-batch")
    item = next(row for row in batch["items"] if row["id"] == item_id)
    assert batch["status"] == "completed"
    assert batch["successful"] == 1
    assert batch["failed"] == 0
    assert item["status"] == "sent"
    assert item["sent"] is True
    assert send_email.call_args.args == (
        "recipient@example.test",
        "Hello",
        "A short, personalized message.",
    )
    assert "Batch processing started" in caplog.text
    assert "A short, personalized message." in caplog.text
    assert "Batch item sent" in caplog.text


def test_hunter_domain_search_includes_company_metadata(monkeypatch, caplog):
    monkeypatch.setattr(settings, "HUNTER_API_KEY", "test-api-key")
    response = Mock(
        json=Mock(
            return_value={
                "data": {
                    "organization": "Example Corporation",
                    "description": "Builds reliable software.",
                    "emails": [
                        {
                            "value": "person@example.test",
                            "first_name": "Ari",
                            "last_name": "Example",
                        }
                    ],
                }
            }
        )
    )
    with patch(
        "app.services.hunter_service._request_with_retry",
        new=AsyncMock(return_value=response),
    ) as request:
        with caplog.at_level(logging.DEBUG, logger="hunter_service"):
            contacts = asyncio.run(discover_contacts_for_domain("example.test", limit=1))

    assert contacts[0]["company_name"] == "Example Corporation"
    assert contacts[0]["company_description"] == "Builds reliable software."
    assert "test-api-key" in request.call_args.kwargs["headers"]["Authorization"]
    assert "person@example.test" in caplog.text
    assert "Example Corporation" in caplog.text


def test_hunter_company_discovery_uses_location_and_open_job_title(monkeypatch):
    monkeypatch.setattr(settings, "HUNTER_API_KEY", "test-api-key")
    response = Mock(
        json=Mock(
            return_value={
                "data": [
                    {
                        "domain": "example.test",
                        "organization": "Example Co",
                        "emails_count": {"total": 12},
                    }
                ],
                "meta": {"results": 1},
            }
        )
    )
    with patch(
        "app.services.hunter_service._request_with_retry",
        new=AsyncMock(return_value=response),
    ) as request:
        companies = asyncio.run(discover_companies("Canada", "Marketing Director", limit=3))

    assert companies == [
        {
            "domain": "example.test",
            "company_name": "Example Co",
            "emails_count": {"total": 12},
        }
    ]
    request.assert_awaited_once()
    assert request.call_args.args == ("POST", "https://api.hunter.io/v2/discover")
    assert request.call_args.kwargs["params"] == {"limit": 3}
    assert request.call_args.kwargs["json"] == {
        "query": "Companies in Canada hiring for Marketing Director roles"
    }


def test_company_discovery_endpoint_returns_selectable_domains(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    companies = [
        {
            "domain": "example.test",
            "company_name": "Example Co",
            "emails_count": {"total": 12},
        }
    ]
    with TestClient(app) as client, patch(
        "app.routers.batches.discover_companies",
        new=AsyncMock(return_value=companies),
    ) as hunter_discovery:
        response = client.get(
            "/api/companies/discover?location=Canada&job_title=Marketing%20Director&limit=3"
        )

    assert response.status_code == 200
    assert response.json()["companies"] == companies
    hunter_discovery.assert_awaited_once_with(
        location="Canada",
        job_title="Marketing Director",
        limit=3,
    )


def test_company_discovery_requires_a_search_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    with TestClient(app) as client, patch(
        "app.routers.batches.discover_companies",
        new=AsyncMock(),
    ) as hunter_discovery:
        response = client.get("/api/companies/discover")

    assert response.status_code == 422
    hunter_discovery.assert_not_awaited()


def test_email_draft_uses_schema_and_retries_invalid_json(caplog):
    class MockAsyncClient:
        def __init__(self, **kwargs):
            self.payloads = []
            self.responses = iter(
                [
                    {"message": {"content": '{"response":"A short draft."}'}},
                    {
                        "message": {
                            "content": (
                                '{"subject":"Hello","body":"A concise email.\\n\\nBest,\\nTauqeer\\n'
                                '[Your Portfolio/LinkedIn/Website]",'
                                '"personalization_reason":"Company context."}'
                            )
                        }
                    },
                ]
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

        async def post(self, url, json):
            self.payloads.append(json)
            return Mock(raise_for_status=Mock(), json=Mock(return_value=next(self.responses)))

    client = MockAsyncClient()
    read_instruction = ollama_service._read_instruction
    with patch.object(
        ollama_service,
        "_read_instruction",
        side_effect=lambda name: (
            "A service that helps small businesses automate invoice processing.\n\n"
            "## Sender details\n"
            "GitHub: https://github.com/tauqeer006\n"
            "LinkedIn: https://www.linkedin.com/in/tauqeer006\n"
            "Portfolio: https://tauqeer006.github.io/"
            if name == "service_description.txt"
            else read_instruction(name)
        ),
    ), caplog.at_level(logging.DEBUG, logger="ollama_service"), patch(
        "app.services.ollama_service.httpx.AsyncClient",
        return_value=client,
    ):
        result = asyncio.run(generate_email_draft({"email": "recipient@example.test", "company_name": "Example Co"}))

    assert result == {
        "subject": "Hello",
        "body": (
            "A concise email.\n\nBest,\nTauqeer\n\n"
            "GitHub: https://github.com/tauqeer006\n"
            "LinkedIn: https://www.linkedin.com/in/tauqeer006\n"
            "Portfolio: https://tauqeer006.github.io/"
        ),
        "personalization_reason": "Company context.",
    }
    assert "[Your Portfolio/LinkedIn/Website]" not in result["body"]
    assert client.payloads[0]["format"]["required"] == ["subject", "body", "personalization_reason"]
    assert len(client.payloads) == 2
    assert "schema" in client.payloads[1]["messages"][-1]["content"]
    assert "A concise email." in caplog.text
    assert any(
        "automate invoice processing" in message["content"]
        for message in client.payloads[0]["messages"]
    )


def test_email_draft_fails_clearly_after_invalid_retry():
    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

        async def post(self, url, json):
            return Mock(
                raise_for_status=Mock(),
                json=Mock(return_value={"message": {"content": '{"response":"No fields"}'}}),
            )

    read_instruction = ollama_service._read_instruction
    with patch.object(
        ollama_service,
        "_read_instruction",
        side_effect=lambda name: (
            "A service that helps small businesses automate invoice processing."
            if name == "service_description.txt"
            else read_instruction(name)
        ),
    ), patch("app.services.ollama_service.httpx.AsyncClient", return_value=MockAsyncClient()):
        with pytest.raises(RuntimeError, match="after retry"):
            asyncio.run(generate_email_draft({"email": "recipient@example.test"}))


def test_email_draft_requires_service_description():
    read_instruction = ollama_service._read_instruction
    with patch.object(
        ollama_service,
        "_read_instruction",
        side_effect=lambda name: (
            "REPLACE_WITH_YOUR_SERVICE_DESCRIPTION"
            if name == "service_description.txt"
            else read_instruction(name)
        ),
    ), pytest.raises(RuntimeError, match="Configure your service"):
        ollama_service.build_ai_prompt({"email": "recipient@example.test"})
