import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from app.core.config import UPLOAD_DIR
from app.db.database import save_verification
from app.schemas.verification import (
    BatchVerificationResult,
    ExtractedFieldsResult,
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
        result = verify_label(image_path, application, verification_id).model_copy(
            update={"source_filename": label_image.filename}
        )
        save_verification(verification_id, result.status, result.model_dump())
        return result
    finally:
        image_path.unlink(missing_ok=True)
        image_path.with_name(f"{image_path.stem}-processed.png").unlink(missing_ok=True)


def _process_batch_item(
    verification_id: str,
    filename: str | None,
    content_type: str | None,
    content: bytes,
    application: ExtractedFields,
) -> VerificationResult:
    if content_type not in ALLOWED_CONTENT_TYPES:
        return _rejected_result(verification_id, filename, "Unsupported file type; upload PNG, JPG, or WEBP.")
    if len(content) > 20 * 1024 * 1024:
        return _rejected_result(verification_id, filename, "Image exceeds the 20 MB size limit.")

    extension = Path(filename or "label.png").suffix.lower() or ".png"
    image_path = UPLOAD_DIR / f"{verification_id}{extension}"
    image_path.write_bytes(content)
    try:
        result = verify_label(image_path, application, verification_id).model_copy(update={"source_filename": filename})
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
) -> None:
    def process(item: tuple[str, str | None, str | None, bytes]) -> VerificationResult:
        verification_id, filename, content_type, content = item
        return _process_batch_item(verification_id, filename, content_type, content, application)

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

    background_tasks.add_task(_run_batch, batch_id, items, application)
    return _batch_response(batch_id)


@router.get("/verifications/batch/{batch_id}", response_model=BatchVerificationResult)
def get_batch_status(batch_id: str) -> BatchVerificationResult:
    return _batch_response(batch_id)
