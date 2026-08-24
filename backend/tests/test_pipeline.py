"""Coverage for app.services.pipeline.verify_label - the orchestration layer that ties
preprocessing, OCR, extraction, and comparison into one result and decides overall status.
OCR and preprocessing are mocked here so status-decision logic is tested deterministically,
independent of the installed Tesseract binary (see test_api.py for an end-to-end path using
real OCR through the HTTP layer)."""

from pathlib import Path

import pytest

from app.services.claude_extraction import ClaudeQuality
from app.services.extraction import ExtractedFields
from app.services.image_processing import ImageQuality
from app.services.ocr import OCRWord
from app.services.validation import EXPECTED_WARNING_TEXT
import app.services.pipeline as pipeline

# A fully-populated, self-consistent field set: every application/label pair here is an exact
# match (including a byte-for-byte correct government warning), so pipeline status logic can
# be exercised without depending on the accuracy of extraction itself (covered separately in
# test_extraction_more.py).
_FULLY_MATCHING_FIELDS = ExtractedFields(
    brand_name="OLD TOM DISTILLERY",
    class_type="Kentucky Straight Bourbon Whiskey",
    alcohol_content="45% Alc./Vol.",
    net_contents="750 mL",
    producer="Example Distillery, Kentucky",
    country_of_origin="United States",
    government_warning=EXPECTED_WARNING_TEXT,
)


def test_all_fields_matching_yields_pass_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: _FULLY_MATCHING_FIELDS)

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test")

    assert result.status == "pass"
    assert all(check.status == "match" for check in result.checks)
    assert result.processing_time_ms >= 0


def test_mismatched_field_yields_review_status_not_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A field mismatch on its own (with a valid government warning, so the fail-precedence
    path in _overall_status doesn't apply) should route to review, not an automatic pass."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(
        pipeline,
        "extract_fields",
        lambda words: ExtractedFields(brand_name="DIFFERENT", government_warning=EXPECTED_WARNING_TEXT),
    )
    application = ExtractedFields(brand_name="OLD TOM")

    result = pipeline.verify_label(image_path, application, "ver_test")

    assert result.status == "review"
    assert not all(check.status == "match" for check in result.checks)


def test_unreadable_image_short_circuits_to_review_with_actionable_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 2.5: never auto-approve when image quality is insufficient, and surface an
    actionable message rather than an opaque failure."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(
        pipeline,
        "preprocess_image",
        lambda path: (path, ImageQuality(readable=False, issues=["Image could not be read: bad format"])),
    )

    def fail_if_called(path):
        raise AssertionError("OCR must not run against an image already flagged unreadable")

    monkeypatch.setattr(pipeline, "read_label", fail_if_called)

    result = pipeline.verify_label(image_path, ExtractedFields(), "ver_test")

    assert result.status == "review"
    assert result.quality.image_readable is False
    assert result.message == "Image could not be read: bad format"
    assert result.checks == []


def test_ocr_failure_is_an_actionable_review_state_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 3: processing failures should return actionable errors instead of timing
    out silently or propagating an exception to the API layer."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))

    def raise_ocr_error(path):
        raise RuntimeError("tesseract is not installed")

    monkeypatch.setattr(pipeline, "read_label", raise_ocr_error)

    result = pipeline.verify_label(image_path, ExtractedFields(), "ver_test")

    assert result.status == "review"
    assert "tesseract is not installed" in result.message


def test_local_ocr_reads_the_processed_image_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Performance regression guard (requirements.md section 3, ~5 second target): a second
    full multi-pass OCR read on the raw original used to run as a "best of two" safety net for
    hard images, but measured against real phone photos it was consistently the most expensive
    step and nearly doubled total time on exactly the images that could least afford it (see
    pipeline.py's _verify_with_local_ocr for the measured numbers). Local OCR must now read
    only the preprocessed image, exactly once, regardless of how weak that single read is."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    processed_path = tmp_path / "label-processed.png"
    processed_path.write_bytes(b"fake-processed")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (processed_path, ImageQuality(readable=True, issues=[])))

    poor_words = [OCRWord("O?D", 0.2, (0, 0, 30, 10))]
    call_count = 0

    def fake_read_label(path):
        nonlocal call_count
        call_count += 1
        assert path == processed_path, "local OCR must only ever read the preprocessed image, not the raw original"
        return poor_words, "O?D"

    monkeypatch.setattr(pipeline, "read_label", fake_read_label)
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: ExtractedFields())

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test")

    assert call_count == 1
    # A weak single-pass read (no warning detected) still produces an actionable, correctly
    # routed result rather than a crash or a silent pass - missing warning text takes the
    # "fail" precedence path per _overall_status, same as it would from any extraction backend.
    assert result.status == "fail"


def test_invalid_government_warning_yields_overall_fail_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """requirements.md 2.1 ('See clear pass, review, or fail results') and arch.md's
    validation decision model both describe a distinct Fail outcome for an invalid/missing
    government warning ("Warning -> No -> Fail: warning missing or incorrect"), taking
    precedence over the ordinary field-match/review distinction."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(
        pipeline,
        "read_label",
        lambda path: ([OCRWord("GOVERNMENT", 0.99, (0, 0, 60, 10)), OCRWord("WARNING:", 0.99, (65, 0, 40, 10))], ""),
    )
    # No application brand/ABV/etc. provided, and the label's government warning is present
    # but incomplete/altered - this is exactly the arch.md "Warning -> No -> Fail" case.
    application = ExtractedFields()

    result = pipeline.verify_label(image_path, application, "ver_test")

    assert result.status == "fail"


def test_valid_warning_with_only_missing_fields_is_review_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail is reserved for a bad government warning specifically - fields that are simply
    absent from both application and label (status "missing", not "mismatch") should still
    only route to review."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: ExtractedFields(government_warning=EXPECTED_WARNING_TEXT))

    result = pipeline.verify_label(image_path, ExtractedFields(), "ver_test")

    assert result.status == "review"


def test_uses_claude_extraction_when_requested_and_configured_and_never_touches_local_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With use_claude=True and an API key configured, Claude is the extraction path end to
    end - the local Tesseract pipeline must not run at all (asserted by making it raise if
    called)."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline.claude_extraction, "is_configured", lambda: True)
    monkeypatch.setattr(
        pipeline.claude_extraction,
        "extract_with_claude",
        lambda path: (_FULLY_MATCHING_FIELDS, "raw claude transcription", ClaudeQuality(readable=True, issues=[], confidence=0.95)),
    )

    def fail_if_called(path):
        raise AssertionError("local OCR must not run when Claude extraction succeeds")

    monkeypatch.setattr(pipeline, "read_label", fail_if_called)

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test", use_claude=True)

    assert result.status == "pass"
    assert result.raw_text == "raw claude transcription"
    assert result.quality.ocr_confidence == 0.95


def test_local_ocr_is_used_by_default_even_when_claude_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local Tesseract OCR is the default, fast synchronous path (see verify_label's
    docstring - measured latency on real photos) - Claude must not run just because a key
    happens to be configured. It only runs when explicitly requested via use_claude=True."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline.claude_extraction, "is_configured", lambda: True)

    def fail_if_called(path):
        raise AssertionError("Claude must not run without use_claude=True, even if configured")

    monkeypatch.setattr(pipeline.claude_extraction, "extract_with_claude", fail_if_called)
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: _FULLY_MATCHING_FIELDS)

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test")

    assert result.status == "pass"
    assert result.message is None


def test_falls_back_to_local_ocr_when_claude_extraction_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Claude failure (network, rate limit, auth, an unparseable response) must degrade to
    the local OCR pipeline rather than failing the verification outright, with a clear note
    explaining why the fallback path was used."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    processed_path = tmp_path / "label-processed.png"
    processed_path.write_bytes(b"fake-processed")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (processed_path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline.claude_extraction, "is_configured", lambda: True)

    def failing_claude(path):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(pipeline.claude_extraction, "extract_with_claude", failing_claude)
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: _FULLY_MATCHING_FIELDS)

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test", use_claude=True)

    assert result.status == "pass"
    assert "Claude extraction unavailable" in result.message
    assert "rate limited" in result.message


def test_local_ocr_is_used_directly_when_claude_is_not_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requesting Claude with no API key configured must not error - it degrades to local OCR
    with an explanatory note, same as any other reason Claude isn't available."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline.claude_extraction, "is_configured", lambda: False)

    def fail_if_called(path):
        raise AssertionError("Claude must not be attempted when is_configured() is False")

    monkeypatch.setattr(pipeline.claude_extraction, "extract_with_claude", fail_if_called)
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: _FULLY_MATCHING_FIELDS)

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test", use_claude=True)

    assert result.status == "pass"
    assert "no ANTHROPIC_API_KEY is configured" in result.message


def test_claude_not_requested_and_not_configured_runs_local_ocr_with_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plain default case - no use_claude flag, no API key - should look identical to any
    other local-OCR run, with no fallback note cluttering the result."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline.claude_extraction, "is_configured", lambda: False)
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: _FULLY_MATCHING_FIELDS)

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test")

    assert result.status == "pass"
    assert result.message is None


def test_rapidocr_drives_fields_but_tesseract_still_supplies_the_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """See app.services.rapid_ocr's module docstring: RapidOCR reads normal print more
    accurately, but its small-print space-gluing failure mode specifically breaks the exact-
    match government warning check, so Tesseract's read must win for that one field even when
    RapidOCR also produced a (wrong) warning value of its own."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (image_path, ImageQuality(readable=True, issues=[])))

    tess_words = [OCRWord("TESS", 0.9, (0, 0, 10, 10))]
    rapid_words = [OCRWord("RAPID", 0.95, (0, 0, 10, 10))]
    monkeypatch.setattr(pipeline, "read_label", lambda path: (tess_words, "TESS raw"))
    monkeypatch.setattr(pipeline.rapid_ocr, "is_available", lambda: True)
    monkeypatch.setattr(pipeline.rapid_ocr, "read_label", lambda path: (rapid_words, "RAPID raw"))

    tess_fields = ExtractedFields(government_warning=EXPECTED_WARNING_TEXT)
    rapid_fields = ExtractedFields(
        brand_name="RAPID BRAND",
        class_type="RAPID CLASS",
        government_warning="GOVERNMENT WARNING:GLUEDTOGETHERTEXT",  # must be ignored - Tesseract found one
    )

    def fake_extract_fields(words):
        if words == tess_words:
            return tess_fields
        if words == rapid_words:
            return rapid_fields
        raise AssertionError(f"unexpected words passed to extract_fields: {words}")

    monkeypatch.setattr(pipeline, "extract_fields", fake_extract_fields)

    result = pipeline.verify_label(image_path, ExtractedFields(), "ver_test")

    # The point of this test is the merge itself, not the overall pass/review/fail verdict -
    # brand/class must come from RapidOCR's read, but the warning must come from Tesseract's.
    assert result.extracted_fields.brand_name == "RAPID BRAND"
    assert result.extracted_fields.class_type == "RAPID CLASS"
    assert result.extracted_fields.government_warning == EXPECTED_WARNING_TEXT


def test_rapidocr_warning_used_only_when_tesseract_found_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fallback direction: a glued-together RapidOCR warning read is still better evidence
    than nothing when Tesseract didn't detect any warning at all."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (image_path, ImageQuality(readable=True, issues=[])))

    tess_words = [OCRWord("TESS", 0.9, (0, 0, 10, 10))]
    rapid_words = [OCRWord("RAPID", 0.95, (0, 0, 10, 10))]
    monkeypatch.setattr(pipeline, "read_label", lambda path: (tess_words, "TESS raw"))
    monkeypatch.setattr(pipeline.rapid_ocr, "is_available", lambda: True)
    monkeypatch.setattr(pipeline.rapid_ocr, "read_label", lambda path: (rapid_words, "RAPID raw"))

    tess_fields = ExtractedFields(government_warning=None)
    rapid_fields = ExtractedFields(government_warning="GOVERNMENT WARNING:GLUEDTOGETHERTEXT")

    monkeypatch.setattr(
        pipeline, "extract_fields", lambda words: tess_fields if words == tess_words else rapid_fields
    )

    result = pipeline.verify_label(image_path, ExtractedFields(), "ver_test")

    assert result.extracted_fields.government_warning == "GOVERNMENT WARNING:GLUEDTOGETHERTEXT"


def test_rapidocr_unavailable_never_calls_its_read_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default (RapidOCR extra not installed) case: fields come from Tesseract alone,
    unchanged from before app.services.rapid_ocr existed."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (image_path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: _FULLY_MATCHING_FIELDS)

    def fail_if_called(path):
        raise AssertionError("rapid_ocr.read_label must not run when is_available() is False")

    monkeypatch.setattr(pipeline.rapid_ocr, "read_label", fail_if_called)
    # is_available() already defaults to False via conftest's autouse fixture - left unset here
    # on purpose, to guard that default rather than an explicit override of it.

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test")

    assert result.status == "pass"


def test_rapidocr_failure_falls_back_to_tesseract_only_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RapidOCR raising (a corrupt model file, an onnxruntime crash, etc.) must degrade to a
    Tesseract-only result rather than failing the verification outright."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(pipeline, "preprocess_image", lambda path: (image_path, ImageQuality(readable=True, issues=[])))
    monkeypatch.setattr(pipeline, "read_label", lambda path: ([OCRWord("X", 0.99, (0, 0, 10, 10))], "X"))
    monkeypatch.setattr(pipeline, "extract_fields", lambda words: _FULLY_MATCHING_FIELDS)
    monkeypatch.setattr(pipeline.rapid_ocr, "is_available", lambda: True)

    def failing_rapid(path):
        raise RuntimeError("onnxruntime session crashed")

    monkeypatch.setattr(pipeline.rapid_ocr, "read_label", failing_rapid)

    result = pipeline.verify_label(image_path, _FULLY_MATCHING_FIELDS, "ver_test")

    assert result.status == "pass"
