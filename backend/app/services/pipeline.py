import time
from dataclasses import asdict
from pathlib import Path

from app.schemas.verification import (
    ExtractedFieldsResult,
    FieldCheckResult,
    QualityResult,
    VerificationResult,
)
from app.services import claude_extraction, rapid_ocr
from app.services.extraction import ExtractedFields, extract_fields
from app.services.image_processing import preprocess_image
from app.services.ocr import read_label
from app.services.validation import compare_fields


def verify_label(
    image_path: Path,
    application: ExtractedFields,
    verification_id: str,
    use_claude: bool = False,
) -> VerificationResult:
    """Local OCR (Tesseract, plus RapidOCR when installed - see app.services.rapid_ocr) is the
    default, synchronous extraction path - measured against real label photos it's the only
    path that reliably lands near the ~5 second target in requirements.md section 3 (Claude
    vision averaged ~10s and ranged up to ~21s on real phone photos in testing, even after
    switching to Sonnet 5 and capping thinking effort - see config.py). Claude vision is a
    real accuracy upgrade beyond even the local RapidOCR+Tesseract combination (it reads the
    label directly rather than classifying OCR'd text line-by-line, so it doesn't share either
    engine's misclassification or space-gluing failure modes), but that accuracy costs several
    times the latency and a real per-call API cost, so it is only used when the caller
    explicitly opts in via use_claude=True (see the request flag threaded through from
    app/api/routes.py) - not automatically just because ANTHROPIC_API_KEY happens to be set.
    An agent who hits a genuinely hard image can re-run with Claude requested."""
    started = time.perf_counter()
    processed_path, quality = preprocess_image(image_path)
    if not quality.readable:
        return _result(verification_id, "review", quality, [], ExtractedFields(), "", started, quality.issues[0])

    fallback_note = None
    if use_claude:
        if claude_extraction.is_configured():
            try:
                return _verify_with_claude(image_path, application, verification_id, quality, started)
            except Exception as error:
                # A Claude failure (network, rate limit, auth, an unparseable response) degrades
                # to the local OCR pipeline rather than failing the verification outright - the
                # note is carried through _result's message below so the agent can see why this
                # result came from the fallback path instead of silently looking identical.
                fallback_note = f"Claude extraction unavailable ({error}); used local OCR fallback."
        else:
            fallback_note = "Claude extraction was requested but no ANTHROPIC_API_KEY is configured; used local OCR."

    return _verify_with_local_ocr(processed_path, application, verification_id, quality, started, fallback_note)


def _verify_with_claude(
    image_path: Path,
    application: ExtractedFields,
    verification_id: str,
    quality,
    started: float,
) -> VerificationResult:
    extracted, raw_text, claude_quality = claude_extraction.extract_with_claude(image_path)
    checks = compare_fields(application, extracted)
    merged_quality = quality.__class__(
        quality.readable and claude_quality.readable,
        [*quality.issues, *claude_quality.issues],
        claude_quality.confidence,
    )
    status, message = _overall_status(checks, claude_quality.confidence)
    return _result(verification_id, status, merged_quality, checks, extracted, raw_text, started, message)


def _verify_with_local_ocr(
    processed_path: Path,
    application: ExtractedFields,
    verification_id: str,
    quality,
    started: float,
    fallback_note: str | None,
) -> VerificationResult:
    # Exactly one Tesseract pass on the preprocessed image is always run - a second full
    # multi-pass Tesseract read on the raw original was tried here previously as a "best of
    # two" safety net for hard images, but measured against real phone photos it was
    # consistently the single most expensive step (nearly doubling total time, e.g.
    # 2.4s -> 6.7s total on a 4032x3024 photo) and it disproportionately triggered on exactly
    # the hardest images - the ones that could least afford it. It's not brought back here:
    # Tesseract still only ever runs once per verification.
    try:
        tess_words, tess_raw_text = read_label(processed_path)
    except Exception as error:  # OCR errors become actionable review states.
        message = f"OCR unavailable: {error}"
        if fallback_note:
            message = f"{fallback_note} {message}"
        return _result(verification_id, "review", quality, [], ExtractedFields(), "", started, message)

    tess_fields = extract_fields(tess_words)

    # RapidOCR (app.services.rapid_ocr), when installed, runs alongside Tesseract rather than
    # instead of it - it reads normal print meaningfully more accurately, so it drives every
    # field except the government warning, but it also has a real failure mode on small dense
    # print (gluing words together with no space at all) that specifically breaks the exact-
    # text warning compliance check. Tesseract's word-level boxes don't share that failure
    # mode, so the warning field always comes from Tesseract's read - see rapid_ocr.py's
    # module docstring for the measured comparison that drove this split. If RapidOCR isn't
    # installed or its read fails, every field falls back to Tesseract's read, unchanged from
    # before this module existed.
    rapid_words: list = []
    rapid_raw_text = ""
    if rapid_ocr.is_available():
        try:
            rapid_words, rapid_raw_text = rapid_ocr.read_label(processed_path)
        except Exception:
            rapid_words = []

    if rapid_words:
        rapid_fields = extract_fields(rapid_words)
        extracted = ExtractedFields(
            brand_name=rapid_fields.brand_name,
            class_type=rapid_fields.class_type,
            alcohol_content=rapid_fields.alcohol_content,
            net_contents=rapid_fields.net_contents,
            producer=rapid_fields.producer,
            country_of_origin=rapid_fields.country_of_origin,
            # Prefer Tesseract's warning read; only fall back to RapidOCR's if Tesseract found
            # none at all, since a glued-together RapidOCR read is still better evidence than
            # nothing for the agent to look at, even though it can never pass the exact-match
            # check.
            government_warning=tess_fields.government_warning or rapid_fields.government_warning,
        )
        words_for_confidence = tess_words + rapid_words
        raw_text = f"{rapid_raw_text}\n\n[Tesseract pass, used for the government warning field]\n{tess_raw_text}"
    else:
        extracted = tess_fields
        words_for_confidence = tess_words
        raw_text = tess_raw_text

    checks = compare_fields(application, extracted)
    average_confidence = _ocr_confidence(words_for_confidence)
    merged_quality = quality.__class__(quality.readable, quality.issues, average_confidence)
    status, message = _overall_status(checks, average_confidence)
    if fallback_note:
        message = f"{fallback_note} {message}" if message else fallback_note
    return _result(verification_id, status, merged_quality, checks, extracted, raw_text, started, message)


# Below this average per-word OCR confidence, a run of matching fields is more likely a
# coincidence of sparse/garbled text than a genuine reliable read. Requirement 2.5 is explicit
# that low OCR confidence must not auto-approve a result, so this is a hard gate on "pass"
# specifically - it never turns a result into "fail" on its own, since fail is reserved for a
# confirmed bad government warning (see below). This same threshold and gate apply to Claude's
# self-reported confidence too, since both signals are a 0-1 "how much should this be trusted"
# score even though they come from very different mechanisms (per-word OCR confidence vs. the
# model's own assessment).
_LOW_CONFIDENCE_THRESHOLD = 0.35


def _overall_status(checks: list, average_confidence: float) -> tuple[str, str | None]:
    """Government warning validity takes precedence over every other field, matching
    arch.md's decision model (Warning exact? No -> Fail; Match fields? No/uncertain ->
    Review; Yes -> Pass). A missing or altered warning is a distinct compliance failure,
    not merely an ambiguous case that needs a second look."""
    warning_check = next((check for check in checks if check.field == "government_warning"), None)
    if warning_check is not None and warning_check.status != "match":
        return "fail", None
    if checks and all(check.status == "match" for check in checks):
        if average_confidence < _LOW_CONFIDENCE_THRESHOLD:
            return "review", "Extraction confidence is low; fields matched but this result needs manual confirmation before approving."
        return "pass", None
    return "review", None


def _ocr_confidence(words) -> float:
    return sum(word.confidence for word in words) / len(words) if words else 0.0


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
