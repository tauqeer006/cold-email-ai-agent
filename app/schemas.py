from __future__ import annotations

from typing import Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator


class BatchStartRequest(BaseModel):
    email_count: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("email_count", "contact_count"),
    )
    domain: str = Field(min_length=1)

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str) -> str:
        domain = value.strip().lower()
        if not domain:
            raise ValueError("A company domain is required for Hunter search.")
        return domain


class SendPreviewEmailRequest(BaseModel):
    batch_id: str = Field(min_length=1, max_length=100)
    item_id: str = Field(min_length=1, max_length=100)

    @field_validator("batch_id", "item_id")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        identifier = value.strip()
        if not identifier:
            raise ValueError("Batch and item identifiers cannot be empty.")
        return identifier


class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str
    processed: int
    successful: int
    failed: int
    remaining: int
    total_requested: int
    retry_attempts: int
    note: str


class BatchItemResponse(BaseModel):
    id: str
    status: str
    approval_status: str
    attempts: int
    email_subject: Optional[str]
    email_body: Optional[str]
    personalization_reason: Optional[str]
    error_message: Optional[str]
    sent: bool
