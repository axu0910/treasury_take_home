import time
from dataclasses import asdict
from pathlib import Path

from app.schemas.verification import (
    ExtractedFieldsResult,
    FieldCheckResult,
    QualityResult,
    VerificationResult,
)
from app.services.extraction import ExtractedFields, extract_fields
from app.services.image_processing import preprocess_image
from app.services.ocr import read_label
from app.services.validation import compare_fields


def verify_label(
    image_path: Path,
    application: ExtractedFields,
    verification_id: str,
) -> VerificationResult:
    started = time.perf_counter()
    processed_path, quality = preprocess_image(image_path)
    if not quality.readable:
        return _result(verification_id, "review", quality, [], ExtractedFields(), "", started, quality.issues[0])

    try:
        candidates = [read_label(processed_path)]
        if processed_path != image_path:
            candidates.append(read_label(image_path))
        words, raw_text = max(candidates, key=_candidate_quality)
    except Exception as error:  # OCR errors become actionable review states.
        return _result(verification_id, "review", quality, [], ExtractedFields(), "", started, f"OCR unavailable: {error}")

    extracted = extract_fields(words)
    checks = compare_fields(application, extracted)
    average_confidence = _ocr_confidence(words)
    quality = quality.__class__(quality.readable, quality.issues, average_confidence)
    status = "pass" if checks and all(check.status == "match" for check in checks) else "review"
    return _result(verification_id, status, quality, checks, extracted, raw_text, started)


def _ocr_confidence(words) -> float:
    return sum(word.confidence for word in words) / len(words) if words else 0.0


def _candidate_quality(candidate) -> tuple[int, float]:
    words, _ = candidate
    extracted = extract_fields(words)
    field_count = sum(
        value is not None
        for value in (
            extracted.brand_name,
            extracted.class_type,
            extracted.alcohol_content,
            extracted.net_contents,
            extracted.producer,
            extracted.country_of_origin,
            extracted.government_warning,
        )
    )
    return field_count, _ocr_confidence(words)


def _result(verification_id, status, quality, checks, extracted, raw_text, started, message=None):
    return VerificationResult(
        verification_id=verification_id,
        status=status,
        processing_time_ms=round((time.perf_counter() - started) * 1000),
        quality=QualityResult(image_readable=quality.readable, issues=quality.issues, ocr_confidence=quality.ocr_confidence),
        checks=[FieldCheckResult(**asdict(check)) for check in checks],
        extracted_fields=ExtractedFieldsResult(**asdict(extracted)),
        raw_text=raw_text,
        message=message,
    )