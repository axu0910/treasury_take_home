from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import UPLOAD_DIR
from app.db.database import save_verification
from app.schemas.verification import BatchVerificationResult, VerificationResult
from app.services.extraction import ExtractedFields
from app.services.pipeline import verify_label

router = APIRouter()


def _application_fields(brand_name, class_type, alcohol_content, net_contents, producer, country_of_origin):
    return ExtractedFields(brand_name, class_type, alcohol_content, net_contents, producer, country_of_origin)


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
    allowed_types = {"image/png", "image/jpeg", "image/webp"}
    if label_image.content_type not in allowed_types:
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


@router.post("/verifications/batch", response_model=BatchVerificationResult)
async def create_batch_verification(
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
    results: list[VerificationResult] = []
    for label_image in label_images:
        content = await label_image.read()
        if label_image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            continue
        verification_id = f"ver_{uuid4().hex[:12]}"
        extension = Path(label_image.filename or "label.png").suffix.lower() or ".png"
        image_path = UPLOAD_DIR / f"{verification_id}{extension}"
        image_path.write_bytes(content)
        try:
            result = verify_label(image_path, application, verification_id).model_copy(
                update={"source_filename": label_image.filename}
            )
            save_verification(verification_id, result.status, result.model_dump())
            results.append(result)
        finally:
            image_path.unlink(missing_ok=True)
            image_path.with_name(f"{image_path.stem}-processed.png").unlink(missing_ok=True)

    return BatchVerificationResult(batch_id=batch_id, total=len(label_images), completed=len(results), results=results)
