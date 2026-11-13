from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("smtp_service")


def send_email_via_smtp(to_email: str, subject: str, body: str, from_name: str | None = None) -> dict[str, str]:
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        logger.error("SMTP send unavailable: credentials are not configured")
        return {"status": "error", "message": "SMTP credentials are not configured."}
    if not settings.SMTP_FROM_EMAIL:
        logger.error("SMTP send unavailable: SMTP_FROM_EMAIL is not configured")
        return {"status": "error", "message": "SMTP_FROM_EMAIL is not configured."}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name or settings.MAIL_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg.set_content(body)

    logger.debug(
        "SMTP send started host=%s port=%s tls=%s from=%s to=%s subject=%r",
        settings.SMTP_HOST,
        settings.SMTP_PORT,
        settings.SMTP_USE_TLS,
        settings.SMTP_FROM_EMAIL,
        to_email,
        subject,
    )
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
        logger.info("SMTP server accepted message host=%s port=%s", settings.SMTP_HOST, settings.SMTP_PORT)
        return {"status": "sent", "message": "Email sent successfully."}
    except Exception as exc:
        logger.exception("SMTP send failed host=%s port=%s", settings.SMTP_HOST, settings.SMTP_PORT)
        return {"status": "error", "message": str(exc)}
