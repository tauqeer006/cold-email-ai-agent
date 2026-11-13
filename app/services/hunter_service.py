from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import settings

logger = logging.getLogger("hunter_service")


def _retry_after_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(float(retry_after), 0)
        except ValueError:
            pass
    return settings.HUNTER_RETRY_BASE_DELAY * (2 ** (attempt - 1))


async def _request_with_retry(method: str, url: str, **kwargs: Any) -> httpx.Response:
    last_error: Exception | None = None
    endpoint = urlsplit(url).path.rsplit("/", 1)[-1]
    for attempt in range(1, settings.HUNTER_MAX_RETRIES + 2):
        try:
            started_at = time.perf_counter()
            async with httpx.AsyncClient(timeout=settings.HUNTER_REQUEST_TIMEOUT) as client:
                response = await client.request(method, url, **kwargs)
            logger.debug(
                "Hunter response method=%s endpoint=%s status=%s attempt=%s duration_ms=%.2f",
                method,
                endpoint,
                response.status_code,
                attempt,
                (time.perf_counter() - started_at) * 1000,
            )
            if response.status_code in {401, 403}:
                raise ValueError("Invalid Hunter.io API key or unauthorized access.")
            if response.status_code == 429:
                if attempt <= settings.HUNTER_MAX_RETRIES:
                    delay = _retry_after_delay(response, attempt)
                    logger.warning("Hunter API rate limit hit; retrying in %s seconds", delay)
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
            if response.status_code in {500, 502, 503, 504}:
                if attempt <= settings.HUNTER_MAX_RETRIES:
                    delay = _retry_after_delay(response, attempt)
                    logger.warning("Hunter API transient server error; retrying in %s seconds", delay)
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
            if 400 <= response.status_code < 500:
                response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, ValueError) as exc:
            last_error = exc
            if attempt <= settings.HUNTER_MAX_RETRIES:
                delay = settings.HUNTER_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "Hunter request failed method=%s endpoint=%s attempt=%s error_type=%s; retrying in %s seconds",
                    method,
                    endpoint,
                    attempt,
                    type(exc).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise RuntimeError(f"Hunter request failed after retries: {exc}") from exc
    if last_error:
        raise RuntimeError(f"Hunter request failed: {last_error}")
    raise RuntimeError("Hunter request failed unexpectedly")


async def search_domain(domain: str, limit: int = 10) -> list[dict[str, Any]]:
    if not settings.HUNTER_API_KEY:
        raise RuntimeError("HUNTER_API_KEY is not configured.")

    logger.info("Hunter domain search started domain=%s limit=%s", domain, limit)
    params = {"domain": domain, "limit": limit}
    headers = {"Authorization": f"Bearer {settings.HUNTER_API_KEY}"}
    response = await _request_with_retry("GET", "https://api.hunter.io/v2/domain-search", headers=headers, params=params)
    payload = response.json()
    data = payload.get("data") or {}
    organization = data.get("organization") or data.get("company") or domain
    description = data.get("description") or data.get("company_description") or ""
    records = [
        {
            **record,
            "company": record.get("company") or organization,
            "company_description": record.get("company_description") or description,
        }
        for record in data.get("emails", [])
    ]
    logger.info(
        "Hunter domain search completed domain=%s organization=%s result_count=%s",
        domain,
        organization,
        len(records),
    )
    logger.debug("Hunter domain search data domain=%s data=%r", domain, data)
    return records


async def discover_companies(
    location: str | None = None,
    job_title: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not settings.HUNTER_API_KEY:
        raise RuntimeError("HUNTER_API_KEY is not configured.")

    if not location and not job_title:
        raise ValueError("Provide a location or an open job title to discover companies.")
    if location and job_title:
        query = f"Companies in {location} hiring for {job_title} roles"
    elif location:
        query = f"Companies in {location}"
    else:
        query = f"Companies hiring for {job_title} roles"
    logger.info("Hunter company discovery started limit=%s query=%s", limit, query)
    headers = {"Authorization": f"Bearer {settings.HUNTER_API_KEY}"}
    response = await _request_with_retry(
        "POST",
        "https://api.hunter.io/v2/discover",
        headers=headers,
        params={"limit": limit},
        json={"query": query},
    )
    payload = response.json()
    companies = payload.get("data") or []
    normalized = [
        {
            "domain": (company.get("domain") or "").strip().lower(),
            "company_name": company.get("organization") or company.get("company") or "",
            "emails_count": company.get("emails_count") or {},
        }
        for company in companies
        if company.get("domain")
    ]
    logger.info("Hunter company discovery completed result_count=%s", len(normalized))
    logger.debug("Hunter company discovery results=%r meta=%r", normalized, payload.get("meta"))
    return normalized


async def verify_email(email: str) -> dict[str, Any]:
    if not settings.HUNTER_API_KEY:
        raise RuntimeError("HUNTER_API_KEY is not configured.")

    url = "https://api.hunter.io/v2/email-verifier"
    headers = {"Authorization": f"Bearer {settings.HUNTER_API_KEY}"}
    params = {"email": email}
    logger.debug("Hunter email verification started email=%s", email)
    response = await _request_with_retry("GET", url, headers=headers, params=params)
    data = response.json().get("data", {})
    logger.debug("Hunter email verification completed email=%s result=%r", email, data)
    return data


async def discover_contacts_for_domain(domain: str, limit: int = 10) -> list[dict[str, Any]]:
    records = await search_domain(domain, limit=limit)
    contacts: list[dict[str, Any]] = []
    for record in records:
        first_name = record.get("first_name") or ""
        last_name = record.get("last_name") or ""
        email = (record.get("value") or record.get("email") or "").strip()
        if not email:
            continue
        position = record.get("position") or ""
        department = record.get("department") or ""
        confidence = int(record.get("confidence_score") or record.get("confidence") or 0)
        company_name = record.get("company") or domain
        contacts.append(
            {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "company_name": company_name,
                "domain": domain,
                "position": position,
                "department": department,
                "source": "hunter",
                "confidence": confidence,
                "company_description": record.get("company_description") or "",
            }
        )
    logger.info("Hunter contacts normalized domain=%s count=%s", domain, len(contacts))
    logger.debug("Hunter normalized contacts domain=%s contacts=%r", domain, contacts)
    return contacts
