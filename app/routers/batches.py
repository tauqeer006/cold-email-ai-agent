from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas import BatchStartRequest, SendPreviewEmailRequest
from app.services.batch_service import approve_batch_item, pause_batch, process_batch_job, resume_batch, send_approved_emails
from app.services.hunter_service import HunterAPIError, discover_companies, discover_contacts_for_domain
from app.services.ollama_service import generate_email_draft
from app.services.smtp_service import send_email_via_smtp
from app.storage import (
    claim_preview_item_for_sending,
    create_batch,
    get_batch,
    get_batch_rows,
    get_used_emails,
    list_batches,
    update_batch_item,
)

router = APIRouter(prefix="", tags=["batches"])
logger = logging.getLogger("cold_email.batches")


async def _discover_recipients(domain: str, limit: int) -> list[dict[str, Any]]:
    discovered = await discover_contacts_for_domain(domain, limit=limit)
    recipients = []
    seen_emails = set()
    for recipient in discovered:
        email = (recipient.get("email") or "").strip()
        if not email or email.casefold() in seen_emails:
            continue
        seen_emails.add(email.casefold())
        recipient["email"] = email
        recipients.append(recipient)
    return recipients[:limit]


@router.post("/batches")
async def start_batch(payload: BatchStartRequest) -> dict[str, Any]:
    requested_count = payload.email_count
    domain = payload.domain.strip().lower()
    logger.info("Hunter batch request received domain=%s email_count=%s", domain, requested_count)
    logger.info("Starting Hunter discovery domain=%s limit=%s", domain, requested_count)
    recipients = await _discover_recipients(domain, requested_count)
    logger.info("Hunter discovery returned domain=%s recipient_count=%s", domain, len(recipients))
    logger.debug("Hunter recipients returned domain=%s recipients=%r", domain, recipients)

    if not recipients:
        logger.warning(
            "Batch creation rejected: Hunter found no email addresses domain=%s requested_count=%s",
            domain,
            requested_count,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Hunter found no email addresses for {domain}. Check the domain and HUNTER_API_KEY.",
        )

    used_emails = get_used_emails()
    unused_recipients = [
        recipient
        for recipient in recipients
        if recipient["email"].casefold() not in used_emails
    ]
    skipped_count = len(recipients) - len(unused_recipients)
    if not unused_recipients:
        logger.info(
            "Batch creation rejected: all Hunter results were previously used domain=%s skipped_count=%s",
            domain,
            skipped_count,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hunter found no new email addresses; all returned addresses are already in the saved CSV files.",
        )

    batch_id = str(uuid.uuid4())
    saved_recipients = create_batch(batch_id, unused_recipients)
    if not saved_recipients:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="These email addresses were added by another request. Search again for unused addresses.",
        )
    skipped_count += len(unused_recipients) - len(saved_recipients)
    logger.info(
        "Batch queued batch_id=%s domain=%s recipient_count=%s",
        batch_id,
        domain,
        len(saved_recipients),
    )
    logger.info("Previously used Hunter results skipped domain=%s skipped_count=%s", domain, skipped_count)
    logger.debug("Batch recipients selected batch_id=%s recipients=%r", batch_id, saved_recipients)
    asyncio.create_task(process_batch_job(batch_id))
    return {
        "batch_id": batch_id,
        "status": "queued",
        "total_requested": len(saved_recipients),
        "skipped_existing_count": skipped_count,
        "emails": [
            {
                "first_name": recipient.get("first_name"),
                "last_name": recipient.get("last_name"),
                "email": recipient.get("email"),
                "company_name": recipient.get("company_name"),
                "domain": recipient.get("domain"),
                "position": recipient.get("position"),
                "department": recipient.get("department"),
                "company_description": recipient.get("company_description"),
            }
            for recipient in saved_recipients
        ],
    }


@router.get("/api/companies/discover")
async def discover_company_domains(
    request: Request,
    location: str | None = Query(default=None, max_length=200),
    job_title: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, Any]:
    location = location.strip() if location else None
    job_title = job_title.strip() if job_title else None
    if not location and not job_title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a location or an open job title to discover companies.",
        )

    request_id = request.state.request_id
    logger.info(
        "Company domain discovery started request_id=%s location=%s job_title=%s limit=%s",
        request_id,
        location,
        job_title,
        limit,
    )
    companies = await discover_companies(location=location, job_title=job_title, limit=limit)
    logger.info(
        "Company domain discovery completed request_id=%s result_count=%s",
        request_id,
        len(companies),
    )
    logger.debug("Company domains returned request_id=%s companies=%r", request_id, companies)
    return {
        "filters": {"location": location, "job_title": job_title},
        "result_count": len(companies),
        "companies": companies,
    }


@router.get("/api/emails/preview")
async def preview_emails(
    request: Request,
    domain: str = Query(..., min_length=1),
    email_count: int = Query(default=1, ge=1),
) -> dict[str, Any]:
    domain = domain.strip().lower()
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A company domain is required for Hunter search.",
        )

    request_id = request.state.request_id
    preview_started = time.perf_counter()
    logger.info(
        "Email preview started request_id=%s domain=%s email_count=%s",
        request_id,
        domain,
        email_count,
    )
    hunter_started = time.perf_counter()
    logger.info("Email preview Hunter lookup started request_id=%s domain=%s", request_id, domain)
    try:
        recipients = await _discover_recipients(domain, email_count)
    except HunterAPIError:
        raise
    except Exception:
        logger.exception("Email preview Hunter lookup failed request_id=%s domain=%s", request_id, domain)
        raise
    logger.info(
        "Email preview Hunter lookup completed request_id=%s domain=%s recipient_count=%s duration_ms=%.2f",
        request_id,
        domain,
        len(recipients),
        (time.perf_counter() - hunter_started) * 1000,
    )
    logger.debug("Email preview Hunter results request_id=%s recipients=%r", request_id, recipients)
    if not recipients:
        logger.warning("Email preview has no Hunter results request_id=%s domain=%s", request_id, domain)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hunter found no email addresses for {domain}. Check the domain and HUNTER_API_KEY.",
        )

    used_emails = get_used_emails()
    new_recipients = [
        recipient
        for recipient in recipients
        if recipient["email"].casefold() not in used_emails
    ]
    skipped_existing_count = len(recipients) - len(new_recipients)
    logger.info(
        "Email preview filtered previously used recipients request_id=%s new_count=%s skipped_count=%s",
        request_id,
        len(new_recipients),
        skipped_existing_count,
    )
    if not new_recipients:
        return {
            "domain": domain,
            "requested_email_count": email_count,
            "result_count": 0,
            "skipped_existing_count": skipped_existing_count,
            "batch_id": None,
            "message": "All Hunter results were already in the saved CSV files; no new drafts were generated.",
            "emails": [],
        }

    emails = []
    for index, recipient in enumerate(new_recipients, start=1):
        draft_started = time.perf_counter()
        logger.info(
            "Email preview Ollama draft started request_id=%s recipient=%s/%s email=%s company=%s",
            request_id,
            index,
            len(new_recipients),
            recipient.get("email"),
            recipient.get("company_name"),
        )
        try:
            draft = await generate_email_draft(
                {
                    key: recipient.get(key)
                    for key in (
                        "first_name",
                        "last_name",
                        "email",
                        "company_name",
                        "domain",
                        "position",
                        "department",
                        "company_description",
                    )
                },
                company_description=recipient.get("company_description"),
            )
        except Exception:
            logger.exception(
                "Email preview Ollama draft failed request_id=%s recipient=%s/%s email=%s",
                request_id,
                index,
                len(new_recipients),
                recipient.get("email"),
            )
            raise
        logger.info(
            "Email preview Ollama draft completed request_id=%s recipient=%s/%s email=%s duration_ms=%.2f",
            request_id,
            index,
            len(new_recipients),
            recipient.get("email"),
            (time.perf_counter() - draft_started) * 1000,
        )
        logger.debug(
            "Email preview generated draft request_id=%s email=%s draft=%r",
            request_id,
            recipient.get("email"),
            draft,
        )
        emails.append({**recipient, **draft})

    batch_id = str(uuid.uuid4())
    saved_emails = create_batch(
        batch_id,
        emails,
        batch_status="previewed",
        item_status="drafted",
        note="Email drafts saved to CSV. No email has been sent.",
    )
    skipped_existing_count += len(emails) - len(saved_emails)
    logger.info(
        "Email preview drafts saved request_id=%s batch_id=%s saved_count=%s skipped_count=%s",
        request_id,
        saved_emails[0]["batch_id"] if saved_emails else None,
        len(saved_emails),
        skipped_existing_count,
    )

    logger.info(
        "Email preview completed request_id=%s domain=%s result_count=%s duration_ms=%.2f",
        request_id,
        domain,
        len(saved_emails),
        (time.perf_counter() - preview_started) * 1000,
    )
    return {
        "domain": domain,
        "requested_email_count": email_count,
        "result_count": len(saved_emails),
        "skipped_existing_count": skipped_existing_count,
        "batch_id": saved_emails[0]["batch_id"] if saved_emails else None,
        "message": (
            f"Saved {len(saved_emails)} new email draft(s) to CSV; no email has been sent."
            if saved_emails
            else "Hunter results were already saved by another request; no new drafts were generated."
        ),
        "emails": saved_emails,
    }


@router.post("/api/emails/send")
async def send_preview_email(
    payload: SendPreviewEmailRequest,
    request: Request,
) -> dict[str, str]:
    batch = get_batch(payload.batch_id)
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft batch not found.")
    batch_items = get_batch_rows(payload.batch_id)
    item = next((row for row in batch_items if row["item_id"] == payload.item_id), None)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found.")
    if item["status"] == "sent" or item["sent"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This draft has already been sent.")
    if item["status"] != "drafted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This draft is not available to send.",
        )

    claimed_item = claim_preview_item_for_sending(payload.batch_id, payload.item_id)
    if not claimed_item:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This draft is already being sent or has already been sent.",
        )

    request_id = request.state.request_id
    logger.info(
        "Preview email SMTP send started request_id=%s recipient=%s",
        request_id,
        claimed_item["email"],
    )
    try:
        result = await asyncio.to_thread(
            send_email_via_smtp,
            claimed_item["email"],
            claimed_item["email_subject"],
            claimed_item["email_body"],
        )
    except Exception as exc:
        update_batch_item(
            payload.batch_id,
            payload.item_id,
            status="send_failed",
            error_message=str(exc),
        )
        logger.exception(
            "Preview email SMTP send failed request_id=%s recipient=%s",
            request_id,
            claimed_item["email"],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not send the email using the configured SMTP account.",
        ) from exc

    if result.get("status") != "sent":
        error_message = result.get("message") or "SMTP delivery failed."
        update_batch_item(
            payload.batch_id,
            payload.item_id,
            status="send_failed",
            error_message=error_message,
        )
        logger.error(
            "Preview email SMTP send failed request_id=%s recipient=%s error=%s",
            request_id,
            claimed_item["email"],
            error_message,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_message,
        )

    if not update_batch_item(
        payload.batch_id,
        payload.item_id,
        status="sent",
        sent=True,
        sent_at=datetime.utcnow().isoformat(),
        approval_status="not_required",
        error_message="",
    ):
        logger.error(
            "SMTP accepted preview email but CSV status update failed request_id=%s batch_id=%s item_id=%s",
            request_id,
            payload.batch_id,
            payload.item_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SMTP accepted the email, but its CSV status could not be saved. Check your Sent folder before retrying.",
        )

    logger.info(
        "Preview email SMTP send completed request_id=%s recipient=%s",
        request_id,
        claimed_item["email"],
    )
    return {"status": "sent", "message": "Email sent from the configured SMTP account."}


@router.get("/batches")
async def list_recent_batches() -> list[dict[str, Any]]:
    return list_batches()


@router.get("/batches/{batch_id}")
async def get_batch_route(batch_id: str) -> dict[str, Any]:
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.post("/batches/{batch_id}/pause")
async def pause_batch_route(batch_id: str) -> dict[str, str]:
    await pause_batch(batch_id)
    return {"status": "paused"}


@router.post("/batches/{batch_id}/resume")
async def resume_batch_route(batch_id: str) -> dict[str, str]:
    await resume_batch(batch_id)
    return {"status": "resumed"}


@router.post("/batches/{batch_id}/approve/{item_id}")
async def approve_batch_item_route(batch_id: str, item_id: str) -> dict[str, str]:
    return await approve_batch_item(batch_id, item_id)


@router.post("/batches/{batch_id}/send-approved")
async def send_approved_route(batch_id: str) -> dict[str, Any]:
    return await send_approved_emails(batch_id)
