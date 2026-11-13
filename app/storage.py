from __future__ import annotations

import csv
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings

BATCH_FIELDS = (
    "batch_id",
    "batch_status",
    "total_requested",
    "note",
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
    "email_subject",
    "email_body",
    "personalization_reason",
    "error_message",
    "created_at",
    "updated_at",
    "sent_at",
    "approved_at",
    "sent",
)

_lock = threading.RLock()


def _path(name: str) -> Path:
    return settings.DATA_DIR / name


def _read_rows(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != list(fields):
            raise RuntimeError(f"CSV file has an invalid header: {path}")
        return [
            {field: row.get(field) or "" for field in fields}
            for row in reader
        ]


def _write_rows(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = file.name
            writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


def _now() -> str:
    return datetime.utcnow().isoformat()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def get_used_emails() -> set[str]:
    with _lock:
        rows = _read_rows(_path("batches.csv"), BATCH_FIELDS)
    return {
        row["email"].strip().casefold()
        for row in rows
        if row["email"].strip()
    }


def create_batch(
    batch_id: str,
    recipients: list[dict[str, Any]],
    *,
    batch_status: str = "queued",
    item_status: str = "pending",
    note: str | None = None,
) -> list[dict[str, Any]]:
    default_note = (
        "Batch queued. Ollama will draft personalized emails and SMTP will send them automatically."
        if batch_status == "queued"
        else "Email drafts saved to CSV. No email has been sent."
    )
    now = _now()
    rows = []
    saved_recipients = []
    with _lock:
        path = _path("batches.csv")
        existing = _read_rows(path, BATCH_FIELDS)
        used_emails = {
            row["email"].strip().casefold()
            for row in existing
            if row["email"].strip()
        }
        for recipient in recipients:
            email = str(recipient.get("email") or "").strip()
            email_key = email.casefold()
            if not email or email_key in used_emails:
                continue
            used_emails.add(email_key)
            item_id = str(uuid.uuid4())
            row = {field: "" for field in BATCH_FIELDS}
            row.update(
                {
                    "batch_id": batch_id,
                    "batch_status": batch_status,
                    "total_requested": "",
                    "note": note or default_note,
                    "item_id": item_id,
                    "first_name": recipient.get("first_name") or "",
                    "last_name": recipient.get("last_name") or "",
                    "email": email,
                    "company_name": recipient.get("company_name") or "",
                    "domain": recipient.get("domain") or "",
                    "position": recipient.get("position") or "",
                    "department": recipient.get("department") or "",
                    "company_description": recipient.get("company_description") or "",
                    "source": recipient.get("source") or "hunter",
                    "confidence": str(_as_int(recipient.get("confidence"))),
                    "status": item_status,
                    "approval_status": "pending",
                    "attempts": "0",
                    "email_subject": recipient.get("subject") or recipient.get("email_subject") or "",
                    "email_body": recipient.get("body") or recipient.get("email_body") or "",
                    "personalization_reason": recipient.get("personalization_reason") or "",
                    "sent": "false",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            rows.append(row)
            saved_recipients.append({**recipient, "email": email, "batch_id": batch_id, "item_id": item_id})

        if rows:
            total_requested = str(len(rows))
            for row in rows:
                row["total_requested"] = total_requested
            _write_rows(path, BATCH_FIELDS, existing + rows)
    return saved_recipients


def claim_preview_item_for_sending(batch_id: str, item_id: str) -> dict[str, Any] | None:
    with _lock:
        path = _path("batches.csv")
        rows = _read_rows(path, BATCH_FIELDS)
        for row in rows:
            if row["batch_id"] != batch_id or row["item_id"] != item_id:
                continue
            if row["status"] != "drafted" or _as_bool(row["sent"]):
                return None
            row["status"] = "sending"
            row["updated_at"] = _now()
            _write_rows(path, BATCH_FIELDS, rows)
            return dict(row)
    return None


def _batch_summary(rows: list[dict[str, str]]) -> dict[str, Any] | None:
    if not rows:
        return None
    first = rows[0]
    processed_rows = [
        row for row in rows
        if row["status"] in {"drafted", "sent", "failed", "send_failed"}
    ]
    sent_count = sum(1 for row in rows if _as_bool(row["sent"]))
    failed_count = sum(1 for row in rows if row["status"] in {"failed", "send_failed"})
    return {
        "batch_id": first["batch_id"],
        "status": first["batch_status"],
        "processed": len(processed_rows),
        "successful": sent_count,
        "failed": failed_count,
        "remaining": max(len(rows) - len(processed_rows), 0),
        "total_requested": _as_int(first["total_requested"], len(rows)),
        "retry_attempts": sum(max(_as_int(row["attempts"]) - 1, 0) for row in rows),
        "note": first["note"],
        "created_at": first["created_at"],
    }


def get_batch(batch_id: str) -> dict[str, Any] | None:
    with _lock:
        rows = [
            row for row in _read_rows(_path("batches.csv"), BATCH_FIELDS)
            if row["batch_id"] == batch_id
        ]
    summary = _batch_summary(rows)
    if summary is None:
        return None
    summary["items"] = [_public_batch_item(row) for row in rows]
    return summary


def _public_batch_item(row: dict[str, str]) -> dict[str, Any]:
    return {
        "id": row["item_id"],
        "recipient": {
            field: row[field]
            for field in (
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
        "status": row["status"],
        "approval_status": row["approval_status"],
        "email_subject": row["email_subject"] or None,
        "email_body": row["email_body"] or None,
        "personalization_reason": row["personalization_reason"] or None,
        "sent": _as_bool(row["sent"]),
        "sent_at": row["sent_at"] or None,
        "error_message": row["error_message"] or None,
    }


def get_batch_rows(batch_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = [
            row for row in _read_rows(_path("batches.csv"), BATCH_FIELDS)
            if row["batch_id"] == batch_id
        ]
    return [
        {
            **row,
            "attempts": _as_int(row["attempts"]),
            "confidence": _as_int(row["confidence"]),
            "sent": _as_bool(row["sent"]),
        }
        for row in rows
    ]


def list_batches() -> list[dict[str, Any]]:
    with _lock:
        rows = _read_rows(_path("batches.csv"), BATCH_FIELDS)
    batches: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        batches.setdefault(row["batch_id"], []).append(row)
    summaries = [_batch_summary(group) for group in batches.values()]
    return sorted(
        (summary for summary in summaries if summary is not None),
        key=lambda summary: summary["created_at"],
        reverse=True,
    )


def update_batch(batch_id: str, **updates: Any) -> bool:
    with _lock:
        path = _path("batches.csv")
        rows = _read_rows(path, BATCH_FIELDS)
        found = False
        for row in rows:
            if row["batch_id"] == batch_id:
                for key, value in updates.items():
                    if key in BATCH_FIELDS:
                        row[key] = str(value if value is not None else "")
                row["updated_at"] = _now()
                found = True
        if found:
            _write_rows(path, BATCH_FIELDS, rows)
        return found


def update_batch_item(batch_id: str, item_id: str, **updates: Any) -> bool:
    with _lock:
        path = _path("batches.csv")
        rows = _read_rows(path, BATCH_FIELDS)
        for row in rows:
            if row["batch_id"] == batch_id and row["item_id"] == item_id:
                for key, value in updates.items():
                    if key in BATCH_FIELDS:
                        row[key] = str(value if value is not None else "")
                row["updated_at"] = _now()
                _write_rows(path, BATCH_FIELDS, rows)
                return True
    return False


def list_active_batches() -> list[str]:
    with _lock:
        rows = _read_rows(_path("batches.csv"), BATCH_FIELDS)
    return list(
        dict.fromkeys(
            row["batch_id"]
            for row in rows
            if row["batch_status"] in {"queued", "running"}
        )
    )
