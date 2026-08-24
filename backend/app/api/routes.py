import csv
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.core.config import UPLOAD_DIR
from app.db.database import get_verification, record_override, save_verification
from app.schemas.verification import (
    BatchVerificationResult,
    ExtractedFieldsResult,
    OverrideInfo,
    OverrideRequest,
    QualityResult,
    VerificationResult,
)
from app.services.extraction import ExtractedFields
from app.services.pipeline import verify_label

router = APIRouter()

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
BATCH_WORKERS = 6

# In-memory batch job registry. This is a prototype-scale simplification: it lives in one
# process's memory (fine for the single-worker deployment this app ships with) rather than a
# durable queue, so batch progress does not survive a restart. Per-item results are still
# persisted to SQLite as each item completes, same as the single-verification path.
_batch_jobs: dict[str, dict] = {}
_batch_lock = threading.Lock()


def _application_fields(brand_name, class_type, alcohol_content, net_contents, producer, country_of_origin):
    return ExtractedFields(brand_name, class_type, alcohol_content, net_contents, producer, country_of_origin)


def _rejected_result(verification_id: str, filename: str | None, message: str) -> VerificationResult:
    """A same-shaped result for an item that was never run, so it still shows up in batch
    results instead of silently vanishing (e.g. an unsupported file type)."""
    return VerificationResult(
        verification_id=verification_id,
        source_filename=filename,
        status="review",
        processing_time_ms=0,
        quality=QualityResult(image_readable=False, issues=[message], ocr_confidence=0.0),
        checks=[],
        extracted_fields=ExtractedFieldsResult(),
        raw_text="",
        message=message,
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local"}


@router.post("/verifications", response_model=VerificationResult)
async def create_verification(
    label_image: UploadFile = File(...),
    brand_name: str | None = Form(default=None),
    class_type: str | None = Form(default=None),
    alcohol_content: str | None = Form(default=None),
    net_contents: str | None = Form(default=None),
    producer: str | None = Form(default=None),
    country_of_origin: str | None = Form(default=None),
    use_claude: bool = Form(default=False),
) -> VerificationResult:
    if label_image.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Upload a PNG, JPG, or WEBP image.")

    content = await label_image.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Images must be 20 MB or smaller.")

    verification_id = f"ver_{uuid4().hex[:12]}"
    extension = Path(label_image.filename or "label.png").suffix.lower() or ".png"
    image_path = UPLOAD_DIR / f"{verification_id}{extension}"
    image_path.write_bytes(content)
    application = _application_fields(brand_name, class_type, alcohol_content, net_contents, producer, country_of_origin)

    try:
        result = verify_label(image_path, application, verification_id, use_claude=use_claude).model_copy(
            update={"source_filename": label_image.filename}
        )
        save_verification(verification_id, result.status, result.model_dump())
        return result
    finally:
        image_path.unlink(missing_ok=True)
        image_path.with_name(f"{image_path.stem}-processed.png").unlink(missing_ok=True)


@router.post("/verifications/{verification_id}/override", response_model=VerificationResult)
def override_verification(verification_id: str, override: OverrideRequest) -> VerificationResult:
    """Requirements.md 2.1: agents can manually correct extracted values or override the
    automated result. Automated status is always a recommendation - this is the recorded,
    final human decision, applied on top of the stored result and written to the append-only
    overrides audit log (see app.db.database.record_override)."""
    stored = get_verification(verification_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Verification not found.")

    result = VerificationResult.model_validate(stored)
    corrected_checks = [
        check.model_copy(
            update={
                "label_value": override.corrected_fields[check.field],
                "status": "match",
                "reason": "Manually corrected by agent.",
            }
        )
        if check.field in override.corrected_fields
        else check
        for check in result.checks
    ]
    override_info = OverrideInfo(
        status=override.status,
        previous_status=result.status,
        note=override.note,
        overridden_by=override.overridden_by,
        corrected_fields=override.corrected_fields,
        created_at=datetime.now(UTC).isoformat(),
    )
    updated = result.model_copy(update={"checks": corrected_checks, "status": override.status, "override": override_info})

    save_verification(verification_id, updated.status, updated.model_dump())
    record_override(
        verification_id, result.status, override.status, override.corrected_fields, override.note, override.overridden_by
    )
    _sync_batch_job_result(verification_id, updated)
    return updated


def _sync_batch_job_result(verification_id: str, updated: VerificationResult) -> None:
    """An overridden item may belong to a batch whose results the frontend is still polling
    (or has already fetched) via GET /verifications/batch/{batch_id} - keep that in-memory
    copy consistent with what was just persisted, so a corrected item doesn't keep showing its
    stale pre-override status."""
    with _batch_lock:
        for job in _batch_jobs.values():
            job["results"] = [updated if item.verification_id == verification_id else item for item in job["results"]]


def _process_batch_item(
    verification_id: str,
    filename: str | None,
    content_type: str | None,
    content: bytes,
    application: ExtractedFields,
    use_claude: bool,
) -> VerificationResult:
    if content_type not in ALLOWED_CONTENT_TYPES:
        return _rejected_result(verification_id, filename, "Unsupported file type; upload PNG, JPG, or WEBP.")
    if len(content) > 20 * 1024 * 1024:
        return _rejected_result(verification_id, filename, "Image exceeds the 20 MB size limit.")

    extension = Path(filename or "label.png").suffix.lower() or ".png"
    image_path = UPLOAD_DIR / f"{verification_id}{extension}"
    image_path.write_bytes(content)
    try:
        result = verify_label(image_path, application, verification_id, use_claude=use_claude).model_copy(
            update={"source_filename": filename}
        )
        save_verification(verification_id, result.status, result.model_dump())
        return result
    except Exception as error:  # noqa: BLE001 - isolate one item's failure from the rest of the batch
        return _rejected_result(verification_id, filename, f"Unexpected processing error: {error}")
    finally:
        image_path.unlink(missing_ok=True)
        image_path.with_name(f"{image_path.stem}-processed.png").unlink(missing_ok=True)


def _run_batch(
    batch_id: str,
    items: list[tuple[str, str | None, str | None, bytes]],
    application: ExtractedFields,
    use_claude: bool,
) -> None:
    def process(item: tuple[str, str | None, str | None, bytes]) -> VerificationResult:
        verification_id, filename, content_type, content = item
        return _process_batch_item(verification_id, filename, content_type, content, application, use_claude)

    with ThreadPoolExecutor(max_workers=BATCH_WORKERS) as executor:
        # executor.map yields results in the original submission order (waiting on an
        # earlier item if needed) even though the work itself runs concurrently, so the
        # frontend can keep matching results[i] to the file it submitted at position i.
        for result in executor.map(process, items):
            with _batch_lock:
                job = _batch_jobs[batch_id]
                job["results"].append(result)
                job["completed"] += 1

    with _batch_lock:
        _batch_jobs[batch_id]["status"] = "completed"


def _batch_response(batch_id: str) -> BatchVerificationResult:
    with _batch_lock:
        job = _batch_jobs.get(batch_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Batch not found.")
        return BatchVerificationResult(
            batch_id=batch_id,
            total=job["total"],
            completed=job["completed"],
            status=job["status"],
            results=list(job["results"]),
        )


@router.post("/verifications/batch", response_model=BatchVerificationResult)
async def create_batch_verification(
    background_tasks: BackgroundTasks,
    label_images: list[UploadFile] = File(...),
    brand_name: str | None = Form(default=None),
    class_type: str | None = Form(default=None),
    alcohol_content: str | None = Form(default=None),
    net_contents: str | None = Form(default=None),
    producer: str | None = Form(default=None),
    country_of_origin: str | None = Form(default=None),
    use_claude: bool = Form(default=False),
) -> BatchVerificationResult:
    if not 1 <= len(label_images) <= 300:
        raise HTTPException(status_code=400, detail="Batch size must be between 1 and 300 images.")

    batch_id = f"batch_{uuid4().hex[:12]}"
    application = _application_fields(brand_name, class_type, alcohol_content, net_contents, producer, country_of_origin)

    items: list[tuple[str, str | None, str | None, bytes]] = []
    for label_image in label_images:
        content = await label_image.read()
        items.append((f"ver_{uuid4().hex[:12]}", label_image.filename, label_image.content_type, content))

    with _batch_lock:
        _batch_jobs[batch_id] = {
            "total": len(items),
            "completed": 0,
            "results": [],
            "status": "processing",
            "created_at": time.time(),
        }

    # use_claude on a 200-300 item batch is a real time/cost commitment (Claude vision
    # averaged ~10s/item in testing - see pipeline.verify_label), unlike the default local-OCR
    # path; the frontend surfaces that tradeoff before an agent opts in for a batch, same
    # toggle as the single-review path.
    background_tasks.add_task(_run_batch, batch_id, items, application, use_claude)
    return _batch_response(batch_id)


@router.get("/verifications/batch/{batch_id}", response_model=BatchVerificationResult)
def get_batch_status(batch_id: str) -> BatchVerificationResult:
    return _batch_response(batch_id)


_EXPORT_FIELDS = (
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "producer",
    "country_of_origin",
    "government_warning",
)


@router.get("/verifications/batch/{batch_id}/export")
def export_batch(batch_id: str, format: str = "csv") -> Response:
    """Requirements.md 2.6: 'Provide a useful export such as CSV or JSON where practical' so
    an agent can work exceptions outside the browser tab (a spreadsheet, a ticketing queue,
    etc.) instead of only ever reading results off the batch dashboard."""
    batch = _batch_response(batch_id)

    if format == "json":
        body = batch.model_dump_json(indent=2)
        media_type = "application/json"
    elif format == "csv":
        body = _batch_to_csv(batch)
        media_type = "text/csv"
    else:
        raise HTTPException(status_code=400, detail="format must be 'csv' or 'json'.")

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{batch_id}.{format}"'},
    )


def _batch_to_csv(batch: BatchVerificationResult) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = ["verification_id", "source_filename", "status", "processing_time_ms", "ocr_confidence", "message"]
    for field in _EXPORT_FIELDS:
        header += [f"{field}_application_value", f"{field}_label_value", f"{field}_status"]
    writer.writerow(header)

    for item in batch.results:
        checks_by_field = {check.field: check for check in item.checks}
        row = [
            item.verification_id,
            item.source_filename or "",
            item.status,
            item.processing_time_ms,
            round(item.quality.ocr_confidence, 4),
            item.message or "",
        ]
        for field in _EXPORT_FIELDS:
            check = checks_by_field.get(field)
            row += [check.application_value if check else "", check.label_value if check else "", check.status if check else ""]
        writer.writerow(row)

    return buffer.getvalue()
