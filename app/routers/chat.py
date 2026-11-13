from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from starlette.responses import StreamingResponse

from app.config import settings
from app.services.ollama_service import chat_with_ollama, stream_chat_with_ollama

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=20)

    @field_validator("messages")
    @classmethod
    def validate_transcript(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        if messages[-1].role != "user":
            raise ValueError("The most recent chat message must be from the user.")
        if sum(len(message.content) for message in messages) > 12000:
            raise ValueError("The chat history must not exceed 12,000 characters.")
        return messages


class ChatResponse(BaseModel):
    reply: str
    model: str


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    try:
        reply = await chat_with_ollama(
            [{"role": message.role, "content": message.content} for message in payload.messages]
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(reply=reply, model=settings.OLLAMA_MODEL)


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    messages = [{"role": message.role, "content": message.content} for message in payload.messages]

    async def stream_events():
        try:
            async for content in stream_chat_with_ollama(messages):
                yield json.dumps({"type": "token", "content": content}) + "\n"
            yield json.dumps({"type": "done", "model": settings.OLLAMA_MODEL}) + "\n"
        except (TimeoutError, ConnectionError, RuntimeError) as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
