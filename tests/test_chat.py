from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


async def mock_chat_stream(messages):
    yield "Hello "
    yield "from Ollama."


def test_chat_returns_model_reply():
    with TestClient(app) as client, patch(
        "app.routers.chat.chat_with_ollama",
        new=AsyncMock(return_value="Hello from Ollama."),
    ):
        response = client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Hello from Ollama."
    assert response.json()["model"]


def test_chat_stream_returns_incremental_events():
    with TestClient(app) as client, patch(
        "app.routers.chat.stream_chat_with_ollama",
        new=mock_chat_stream,
    ):
        response = client.post(
            "/api/chat/stream",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    events = [line for line in response.text.splitlines() if line]
    assert response.status_code == 200
    assert '"content": "Hello "' in events[0]
    assert '"content": "from Ollama."' in events[1]
    assert '"type": "done"' in events[2]


def test_chat_requires_user_as_last_message():
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"messages": [{"role": "assistant", "content": "Hello"}]},
        )

    assert response.status_code == 422
