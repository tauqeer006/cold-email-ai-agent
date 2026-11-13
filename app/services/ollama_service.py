from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from app.config import settings

logger = logging.getLogger("ollama_service")
_ollama_semaphore = asyncio.Semaphore(max(1, settings.MAX_CONCURRENT_AI_REQUESTS))

BASE_DIR = Path(__file__).resolve().parent.parent.parent
INSTRUCTIONS_DIR = BASE_DIR / "instructions"


def _read_instruction(name: str) -> str:
    path = INSTRUCTIONS_DIR / name
    return path.read_text(encoding="utf-8").strip()


def _read_service_description() -> str:
    description = _read_instruction("service_description.txt")
    if not description or "REPLACE_WITH_YOUR_SERVICE_DESCRIPTION" in description:
        raise RuntimeError(
            "Configure your service in instructions/service_description.txt before generating emails."
        )
    return description


def _sender_profile_links(service_description: str) -> list[tuple[str, str]]:
    match = re.search(
        r"(?ms)^## Sender details\s*\n(.*?)(?=^## |\Z)",
        service_description,
    )
    if not match:
        return []

    links = []
    for line in match.group(1).splitlines():
        link_match = re.fullmatch(
            r"\s*(GitHub|LinkedIn|Portfolio)\s*:\s*(https?://\S+)\s*",
            line,
            re.IGNORECASE,
        )
        if link_match:
            label, url = link_match.groups()
            links.append((label, url))
    return links


def _ensure_sender_links(body: str, service_description: str) -> str:
    body = re.sub(r"\[Your Portfolio/LinkedIn/Website\]", "", body, flags=re.IGNORECASE).rstrip()
    missing_links = [
        (label, url)
        for label, url in _sender_profile_links(service_description)
        if url.casefold() not in body.casefold()
    ]
    if missing_links:
        signature = "\n".join(f"{label}: {url}" for label, url in missing_links)
        body = f"{body}\n\n{signature}" if body else signature
    return body


def build_ai_prompt(
    contact: dict[str, Any],
    company_description: str | None = None,
    *,
    service_description: str | None = None,
) -> str:
    system_prompt = _read_instruction("system_prompt.txt")
    email_instructions = _read_instruction("email_generation_instructions.txt")
    personalization_instructions = _read_instruction("personalization_instructions.txt")
    if service_description is None:
        service_description = _read_service_description()
    sender_profile = [
        f"- Name: {settings.MAIL_FROM_NAME}",
        f"- Email: {settings.SMTP_FROM_EMAIL}",
    ]
    if settings.MAIL_FROM_COMPANY:
        sender_profile.append(f"- Company: {settings.MAIL_FROM_COMPANY}")
    if settings.MAIL_FROM_TITLE:
        sender_profile.append(f"- Title: {settings.MAIL_FROM_TITLE}")

    return f"""
{system_prompt}

Sender profile:
{chr(10).join(sender_profile)}

Contact profile:
- First name: {contact.get('first_name') or 'Unknown'}
- Last name: {contact.get('last_name') or 'Unknown'}
- Company: {contact.get('company_name') or 'Unknown'}
- Domain: {contact.get('domain') or 'Unknown'}
- Position: {contact.get('position') or 'Unknown'}
- Department: {contact.get('department') or 'Unknown'}
- Company description: {company_description or contact.get('company_description') or 'Not provided'}

Writing instructions:
{email_instructions}

Service being offered:
{service_description}

Personalization instructions:
{personalization_instructions}
"""


def _extract_json(content: str) -> dict[str, Any]:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        content = match.group(1)
    cleaned = content.strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Model response did not include a JSON object.")
        cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Model response must be a JSON object.")
    return parsed


_EMAIL_DRAFT_FORMAT = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "personalization_reason": {"type": "string"},
    },
    "required": ["subject", "body", "personalization_reason"],
    "additionalProperties": False,
}


def _validate_email_draft(content: str) -> dict[str, str]:
    draft = _extract_json(content)
    required = ("subject", "body", "personalization_reason")
    missing = [field for field in required if not isinstance(draft.get(field), str) or not draft[field].strip()]
    if missing:
        raise ValueError(f"Missing or empty required fields: {missing}")
    return {field: draft[field].strip() for field in required}


async def generate_email_draft(contact: dict[str, Any], company_description: str | None = None) -> dict[str, Any]:
    logger.info("Ollama draft generation started model=%s", settings.OLLAMA_MODEL)
    service_description = _read_service_description()
    messages = [
        {"role": "system", "content": _read_instruction("system_prompt.txt")},
        {
            "role": "user",
            "content": (
                f"{build_ai_prompt(contact, company_description, service_description=service_description)}\n\n"
                "Output exactly one JSON object with these three non-empty string fields: "
                '"subject", "body", and "personalization_reason". Do not use other keys '
                "or put the JSON inside markdown."
            ),
        },
    ]
    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    try:
        async with _ollama_semaphore:
            async with httpx.AsyncClient(timeout=60.0) as client:
                for attempt in range(2):
                    response = await client.post(
                        url,
                        json={
                            "model": settings.OLLAMA_MODEL,
                            "stream": False,
                            "format": _EMAIL_DRAFT_FORMAT,
                            "messages": messages,
                            "options": {"temperature": 0.2, "num_predict": 256},
                        },
                    )
                    response.raise_for_status()
                    result = response.json()
                    content = result.get("message", {}).get("content")
                    logger.debug(
                        "Ollama draft response model=%s email=%s attempt=%s response=%r",
                        settings.OLLAMA_MODEL,
                        contact.get("email"),
                        attempt + 1,
                        content,
                    )
                    if not isinstance(content, str) or not content.strip():
                        validation_error = ValueError("Ollama returned an empty email draft.")
                    else:
                        try:
                            draft = _validate_email_draft(content)
                            draft["body"] = _ensure_sender_links(draft["body"], service_description)
                            logger.debug(
                                "Ollama draft accepted model=%s email=%s draft=%r",
                                settings.OLLAMA_MODEL,
                                contact.get("email"),
                                draft,
                            )
                            return draft
                        except (json.JSONDecodeError, ValueError) as exc:
                            validation_error = exc
                    if attempt == 0:
                        logger.warning(
                            "Ollama returned an invalid email draft for contact %s; retrying with schema reminder",
                            contact.get("email"),
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Your last response did not match the required email JSON schema. "
                                    "Return only a JSON object with non-empty string values for subject, "
                                    "body, and personalization_reason. Do not omit any field."
                                ),
                            }
                        )
                raise ValueError(f"Ollama returned an invalid email draft after retry: {validation_error}")
    except Exception as exc:
        logger.exception("AI email generation failed for contact %s", contact.get("email"))
        raise RuntimeError(f"Failed to generate email draft: {exc}") from exc


async def chat_with_ollama(messages: list[dict[str, str]]) -> str:
    payload = {
        "model": settings.OLLAMA_MODEL,
        "stream": False,
        "messages": messages,
        "options": {"num_predict": settings.OLLAMA_CHAT_MAX_TOKENS},
    }
    url = f"{settings.OLLAMA_BASE_URL}/api/chat"

    try:
        async with _ollama_semaphore:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
        content = result["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama returned an empty response.")
        return content.strip()
    except httpx.TimeoutException as exc:
        logger.warning("Ollama chat timed out")
        raise TimeoutError("Ollama did not respond in time.") from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("Ollama chat returned HTTP %s", exc.response.status_code)
        raise RuntimeError(f"Ollama returned HTTP {exc.response.status_code}.") from exc
    except httpx.RequestError as exc:
        logger.warning("Ollama chat request failed: %s", exc)
        raise ConnectionError("Could not connect to Ollama. Check that the Ollama service is running.") from exc
    except (KeyError, ValueError) as exc:
        logger.warning("Ollama chat returned an invalid response: %s", exc)
        raise RuntimeError("Ollama returned an invalid response.") from exc


async def stream_chat_with_ollama(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    payload = {
        "model": settings.OLLAMA_MODEL,
        "stream": True,
        "messages": messages,
        "options": {"num_predict": settings.OLLAMA_CHAT_MAX_TOKENS},
    }
    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    received_content = False

    try:
        async with _ollama_semaphore:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=20.0)) as client:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise RuntimeError("Ollama returned an invalid streaming response.") from exc
                        if event.get("error"):
                            raise RuntimeError(f"Ollama error: {event['error']}")
                        content = event.get("message", {}).get("content", "")
                        if content:
                            received_content = True
                            yield content
        if not received_content:
            raise RuntimeError("Ollama returned an empty response.")
    except httpx.TimeoutException as exc:
        logger.warning("Ollama chat stream timed out")
        raise TimeoutError("Ollama did not respond in time.") from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("Ollama chat stream returned HTTP %s", exc.response.status_code)
        raise RuntimeError(f"Ollama returned HTTP {exc.response.status_code}.") from exc
    except httpx.RequestError as exc:
        logger.warning("Ollama chat stream request failed: %s", exc)
        raise ConnectionError("Could not connect to Ollama. Check that the Ollama service is running.") from exc
