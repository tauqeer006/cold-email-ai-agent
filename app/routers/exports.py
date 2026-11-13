from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.csv_service import export_batch_csv
from app.storage import get_batch_rows

router = APIRouter(prefix="", tags=["exports"])


@router.get("/batches/{batch_id}/export/csv")
async def export_batch(batch_id: str) -> FileResponse:
    rows = get_batch_rows(batch_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No export rows found for this batch.")
    export_path = export_batch_csv(batch_id, rows)
    return FileResponse(export_path, filename=f"{batch_id}.csv", media_type="text/csv")
