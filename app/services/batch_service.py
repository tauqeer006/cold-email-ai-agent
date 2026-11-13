from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from app.services.hunter_service import verify_email
from app.services.ollama_service import generate_email_draft
from app.services.smtp_service import send_email_via_smtp
from app.storage import get_batch, get_batch_rows, update_batch, update_batch_item

logger = logging.getLogger("batch_service")


async def process_batch_job(batch_id: str) -> None:
    batch = get_batch(batch_id)
    if not batch:
        logger.error("Batch processing stopped: batch not found batch_id=%s", batch_id)
        return
    update_batch(batch_id, batch_status="running")
    items = get_batch_rows(batch_id)
    logger.info("Batch processing started batch_id=%s item_count=%s", batch_id, len(items))

    for item in items:
        current_batch = get_batch(batch_id)
        if not current_batch or current_batch["status"] == "paused":
            logger.info("Batch processing paused batch_id=%s", batch_id)
            break
        if item["status"] in {"sent", "failed", "send_failed"}:
            logger.info(
                "Batch item skipped batch_id=%s item_id=%s status=%s",
                batch_id,
                item["item_id"],
                item["status"],
            )
            continue
        if item["status"] == "sending":
            update_batch_item(
                batch_id,
                item["item_id"],
                status="send_failed",
                error_message="Interrupted during SMTP delivery; verify delivery before retrying.",
            )
            logger.error(
                "Batch item interrupted during SMTP delivery batch_id=%s item_id=%s; not retrying to avoid duplicate email",
                batch_id,
                item["item_id"],
            )
            continue

        stage = "email verification"
        item_id = item["item_id"]
        email = item["email"]
        update_batch_item(
            batch_id,
            item_id,
            attempts=item["attempts"] + 1,
            error_message="",
        )
        try:
            if not email:
                raise ValueError("Hunter did not provide an email address.")
            logger.info("Batch item started batch_id=%s item_id=%s", batch_id, item_id)
            logger.debug(
                "Batch item recipient batch_id=%s item_id=%s recipient=%r",
                batch_id,
                item_id,
                {key: item.get(key) for key in (
                    "first_name",
                    "last_name",
                    "email",
                    "company_name",
                    "domain",
                    "position",
                    "department",
                    "company_description",
                )},
            )
            verification = await verify_email(email)
            logger.info(
                "Hunter verification complete batch_id=%s item_id=%s status=%s score=%s",
                batch_id,
                item_id,
                verification.get("status"),
                verification.get("score"),
            )

            stage = "Ollama draft generation"
            draft = await generate_email_draft(
                {key: item.get(key) for key in (
                    "first_name",
                    "last_name",
                    "email",
                    "company_name",
                    "domain",
                    "position",
                    "department",
                    "company_description",
                )},
                company_description=item.get("company_description"),
            )
            logger.debug("Batch item draft batch_id=%s item_id=%s draft=%r", batch_id, item_id, draft)
            update_batch_item(
                batch_id,
                item_id,
                email_subject=draft["subject"],
                email_body=draft["body"],
                personalization_reason=draft["personalization_reason"],
                status="sending",
            )

            stage = "SMTP delivery"
            logger.info("Batch item sending batch_id=%s item_id=%s", batch_id, item_id)
            result = await asyncio.to_thread(
                send_email_via_smtp,
                email,
                draft["subject"],
                draft["body"],
            )
            if result["status"] != "sent":
                raise RuntimeError(result["message"])

            update_batch_item(
                batch_id,
                item_id,
                status="sent",
                sent=True,
                sent_at=datetime.utcnow().isoformat(),
                approval_status="not_required",
                error_message="",
            )
            logger.info("Batch item sent batch_id=%s item_id=%s", batch_id, item_id)
        except Exception as exc:
            current_item = next(
                (row for row in get_batch_rows(batch_id) if row["item_id"] == item_id),
                {},
            )
            failed_status = "send_failed" if current_item.get("email_subject") else "failed"
            update_batch_item(
                batch_id,
                item_id,
                status=failed_status,
                error_message=str(exc),
            )
            logger.exception(
                "Batch item failed batch_id=%s item_id=%s stage=%s",
                batch_id,
                item_id,
                stage,
            )

    current_batch = get_batch(batch_id)
    if current_batch and current_batch["status"] != "paused":
        final_status = "completed" if current_batch["failed"] == 0 else "completed_with_errors"
        update_batch(
            batch_id,
            batch_status=final_status,
            note="Batch finished. Ollama generated personalized drafts and SMTP attempted delivery.",
        )
        final_batch = get_batch(batch_id)
        logger.info(
            "Batch processing finished batch_id=%s status=%s processed=%s successful=%s failed=%s remaining=%s",
            batch_id,
            final_status,
            final_batch["processed"] if final_batch else 0,
            final_batch["successful"] if final_batch else 0,
            final_batch["failed"] if final_batch else 0,
            final_batch["remaining"] if final_batch else 0,
        )


async def pause_batch(batch_id: str) -> None:
    if not update_batch(batch_id, batch_status="paused", note="Batch paused by user."):
        logger.warning("Cannot pause missing batch batch_id=%s", batch_id)


async def resume_batch(batch_id: str) -> None:
    if not update_batch(batch_id, batch_status="queued", note="Batch resumed."):
        logger.warning("Cannot resume missing batch batch_id=%s", batch_id)
        return
    asyncio.create_task(process_batch_job(batch_id))


async def approve_batch_item(batch_id: str, item_id: str) -> dict[str, Any]:
    if not update_batch_item(
        batch_id,
        item_id,
        approval_status="approved",
        approved_at=datetime.utcnow().isoformat(),
    ):
        return {"status": "error", "message": "Batch item not found."}
    return {"status": "approved", "message": "Email draft approved."}


async def send_approved_emails(batch_id: str) -> dict[str, Any]:
    items = [
        item for item in get_batch_rows(batch_id)
        if item["approval_status"] == "approved" and not item["sent"]
    ]
    if not items:
        return {"status": "error", "message": "No approved items to send."}

    sent_count = 0
    for item in items:
        result = await asyncio.to_thread(
            send_email_via_smtp,
            item["email"],
            item["email_subject"] or "Follow-up",
            item["email_body"] or "",
        )
        if result["status"] == "sent":
            update_batch_item(
                batch_id,
                item["item_id"],
                sent=True,
                sent_at=datetime.utcnow().isoformat(),
                status="sent",
            )
            sent_count += 1
        else:
            update_batch_item(
                batch_id,
                item["item_id"],
                status="send_failed",
                error_message=result["message"],
            )
    return {"status": "ok", "sent_count": sent_count}
