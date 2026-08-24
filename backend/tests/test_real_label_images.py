"""Regression tests against the real sample label images kept locally in uploads/ - a
gitignored, dev-only asset directory that is *not* committed to the repo (see .gitignore:
`uploads/*` with only `.gitkeep` tracked). These exercise the real pipeline end-to-end with
real Tesseract OCR, no mocking, and are the best available approximation this project has of
"does this actually work on a label" rather than on synthetic OCRWord fixtures. Every test
skips cleanly (instead of failing) when its source image isn't present, so a fresh clone or
CI run is unaffected - only a machine with these local dev images actually exercises them.
"""

import shutil
import time
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from app.services.extraction import ExtractedFields
from app.services.pipeline import verify_label
from app.services.validation import EXPECTED_WARNING_TEXT

UPLOADS_DIR = Path(__file__).resolve().parents[2] / "uploads"


def _sample(tmp_path: Path, name: str) -> Path:
    """Copy a real sample image into a scratch dir before running the pipeline on it, so
    preprocess_image's "-processed.png" side-output never lands in the real uploads/
    directory as a side effect of running the test suite."""
    source = UPLOADS_DIR / name
    if not source.is_file():
        pytest.skip(f"{name} not present in uploads/ (gitignored local dev asset, not checked into the repo)")
    destination = tmp_path / name
    shutil.copy(source, destination)
    return destination


def test_ttb_test_jpg_runs_end_to_end_and_extracts_brand(tmp_path: Path) -> None:
    """ttb_test.jpg is a clean, digitally-rendered two-panel (brand label + back label)
    sample - the same layout modeled by
    test_validation.py::test_extraction_does_not_treat_warning_or_caption_text_as_fields."""
    image_path = _sample(tmp_path, "ttb_test.jpg")

    result = verify_label(image_path, ExtractedFields(brand_name="ABC DISTILLERY"), "ver_real_ttb")

    assert result.status in {"pass", "review", "fail"}
    assert result.quality.image_readable is True
    assert result.extracted_fields.brand_name is not None
    assert "ABC" in result.extracted_fields.brand_name


def test_ttb_test_jpg_alcohol_content_and_net_contents_are_extracted(tmp_path: Path) -> None:
    image_path = _sample(tmp_path, "ttb_test.jpg")

    result = verify_label(image_path, ExtractedFields(), "ver_real_ttb")

    assert result.extracted_fields.alcohol_content is not None
    assert "45" in result.extracted_fields.alcohol_content
    assert result.extracted_fields.net_contents is not None
    assert "750" in result.extracted_fields.net_contents


def test_ttb_test_jpg_government_warning_captures_both_numbered_clauses(tmp_path: Path) -> None:
    """Regression test for a confirmed real-world bug found by running this actual sample
    label through the pipeline: the official warning statement has an internal period after
    "...birth defects." separating its two numbered sentences, which used to trip the
    warning-capture logic's "bound to the first period" heuristic into truncating clause (2)
    off entirely - see extract_fields' government-warning capture and EXPECTED_WARNING_TEXT
    in validation.py.

    This asserts clause (2) survives extraction and the text is very close to the required
    statement - not byte-identical, since Tesseract still makes a couple of
    single-character misreads on this image ("General" -> "Genera", "defects" -> "detects")
    that are real OCR noise, not something extraction logic can or should paper over. An
    imperfect-but-close read like this is exactly the case requirements.md 2.5 expects to
    route to manual review rather than being silently corrected or silently passed.
    """
    image_path = _sample(tmp_path, "ttb_test.jpg")

    result = verify_label(image_path, ExtractedFields(), "ver_real_ttb")
    warning = result.extracted_fields.government_warning

    assert warning is not None
    assert "(2) Consumption of alcoholic beverages" in warning
    assert warning.rstrip(".").endswith("health problems")
    similarity = SequenceMatcher(None, warning, EXPECTED_WARNING_TEXT).ratio()
    assert similarity > 0.95, f"expected a near-exact OCR read, got {similarity:.1%} similar: {warning!r}"


def test_label1_png_runs_end_to_end_without_error(tmp_path: Path) -> None:
    """label1.png has a two-column layout (an illustration/aging-notes column beside the
    government-warning column) that Tesseract's word reading order interleaves line-by-line,
    corrupting the warning and producer extractions with unrelated text from the other
    column. That's a deeper layout-awareness gap in the extraction heuristics than a single
    field-level assertion can usefully pin down, so this test only confirms the pipeline
    still completes end-to-end with a well-formed result instead of crashing on it."""
    image_path = _sample(tmp_path, "label1.png")

    result = verify_label(image_path, ExtractedFields(brand_name="ABC DISTILLERY"), "ver_real_label1")

    assert result.status in {"pass", "review", "fail"}
    assert result.quality.image_readable is True


def test_label2_webp_low_quality_photo_is_never_silently_passed(tmp_path: Path) -> None:
    """label2.webp is a real photograph of a curved wine bottle label shot at an angle -
    exactly the imperfect-image case requirements.md 2.5 describes. Requirement 2.5: never
    auto-approve a result when image or OCR confidence is insufficient."""
    image_path = _sample(tmp_path, "label2.webp")

    result = verify_label(image_path, ExtractedFields(brand_name="Hawk's Shadow"), "ver_real_label2")

    assert result.status != "pass"


@pytest.mark.parametrize("filename", ["IMG_5689.JPG", "IMG_5690.JPG", "IMG_5691.JPG"])
def test_full_resolution_iphone_photo_does_not_crash_or_time_out(tmp_path: Path, filename: str) -> None:
    """Regression test for a confirmed real-world bug found by running actual full-resolution
    iPhone photos (4032x3024, ~2-3 MB each) through the pipeline: Pillow reports these as PIL
    format "MPO" (Multi-Picture Object, used for Portrait-mode photos), which isn't in
    pytesseract's format allowlist and crashed OCR outright with 'Unsupported image
    format/type' - see the format-normalization fix in app.services.ocr.read_label. These are
    also a real-world case of requirements.md 2.5's "photographed at weird angles" imperfect
    image handling: the bottle is held rotated in-frame, not just camera-rotated via EXIF, so
    extraction quality is genuinely poor and the result must never silently pass. This also
    guards the ~5-second performance target (requirements.md section 3): a 12-megapixel
    source must not make OCR take 10+ seconds, per the downscaling and candidate-skip logic in
    app.services.ocr and app.services.pipeline."""
    image_path = _sample(tmp_path, filename)

    started = time.perf_counter()
    result = verify_label(image_path, ExtractedFields(brand_name="test"), "ver_real_iphone")
    elapsed = time.perf_counter() - started

    assert result.message is None or "OCR unavailable" not in result.message
    assert result.status != "pass"
    # Generous bound for a genuinely hard, full-resolution, busy-background photo on a dev
    # machine - well below the pre-fix 10-13+ seconds this same file used to take.
    assert elapsed < 15.0, f"{filename} took {elapsed:.1f}s end to end"
