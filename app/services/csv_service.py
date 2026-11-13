from __future__ import annotations

import csv

from app.config import settings


def export_batch_csv(batch_id: str, rows: list[dict]) -> str:
    export_dir = settings.EXPORTS_DIR
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"{batch_id}.csv"
    fieldnames = [
        "batch_id",
        "item_id",
        "first_name",
        "last_name",
        "email",
        "company_name",
        "domain",
        "position",
        "department",
        "company_description",
        "source",
        "confidence",
        "status",
        "approval_status",
        "attempts",
        "subject",
        "body",
        "personalization_reason",
        "error_message",
        "sent",
        "sent_at",
    ]
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "batch_id": row.get("batch_id", ""),
                "item_id": row.get("item_id", ""),
                "first_name": row.get("first_name", ""),
                "last_name": row.get("last_name", ""),
                "email": row.get("email", ""),
                "company_name": row.get("company_name", ""),
                "domain": row.get("domain", ""),
                "position": row.get("position", ""),
                "department": row.get("department", ""),
                "company_description": row.get("company_description", ""),
                "source": row.get("source", ""),
                "confidence": row.get("confidence", ""),
                "status": row.get("status", ""),
                "approval_status": row.get("approval_status", ""),
                "attempts": row.get("attempts", ""),
                "subject": row.get("email_subject", ""),
                "body": row.get("email_body", ""),
                "personalization_reason": row.get("personalization_reason", ""),
                "error_message": row.get("error_message", ""),
                "sent": row.get("sent", False),
                "sent_at": row.get("sent_at", ""),
            })
    return str(path)
