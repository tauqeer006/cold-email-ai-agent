from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(prefix="", tags=["dashboard"])

ROOT = Path(__file__).resolve().parent.parent.parent


@router.get("/")
async def dashboard_root() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "cold-email-automation"}
