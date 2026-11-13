from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.logging_config import configure_logging
from app.routers.batches import router as batches_router
from app.routers.chat import router as chat_router
from app.routers.dashboard import router as dashboard_router
from app.routers.exports import router as exports_router
from app.services.batch_service import process_batch_job
from app.storage import list_active_batches

app = FastAPI(title="Cold Email Automation API", version="1.0.0")
logger = logging.getLogger("cold_email.api")

app.include_router(dashboard_router)
app.include_router(chat_router)
app.include_router(batches_router)
app.include_router(exports_router)

BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.exception(
            "Unhandled request exception request_id=%s method=%s path=%s duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            elapsed_ms,
        )
        raise

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    log_method = logger.warning if response.status_code >= 400 else logger.info
    log_method(
        "request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.on_event("startup")
async def startup_event() -> None:
    configure_logging()
    logger.info("Starting application; file logs: %s", settings.LOGS_DIR / "app.log")
    logger.info("CSV persistence directory: %s", settings.DATA_DIR)
    for batch_id in list_active_batches():
        logger.info("Resuming stored batch batch_id=%s", batch_id)
        asyncio.create_task(process_batch_job(batch_id))
