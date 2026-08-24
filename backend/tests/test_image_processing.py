"""Coverage for app.services.image_processing.preprocess_image, driven by requirements.md
2.5 (Image handling): detect unreadable images, provide quality feedback, and support basic
preprocessing (here: EXIF-orientation correction, RGBA flattening, contrast normalization)."""

from pathlib import Path

from PIL import Image

from app.services.image_processing import preprocess_image


def test_readable_image_is_marked_readable_with_no_issues(tmp_path: Path) -> None:
    image_path = tmp_path / "label.png"
    Image.new("RGB", (600, 600), "white").save(image_path)

    processed_path, quality = preprocess_image(image_path)

    assert quality.readable is True
    assert quality.issues == []
    assert processed_path.exists()
    assert processed_path.suffix == ".png"


def test_low_resolution_image_reports_a_quality_issue_but_stays_readable(tmp_path: Path) -> None:
    """Requirement 2.5: provide image-quality feedback rather than an opaque failure."""
    image_path = tmp_path / "label.png"
    Image.new("RGB", (150, 150), "white").save(image_path)

    _, quality = preprocess_image(image_path)

    assert quality.readable is True
    assert any("resolution" in issue.lower() for issue in quality.issues)


def test_unreadable_file_is_reported_as_not_readable(tmp_path: Path) -> None:
    """Requirement 2.5: detect unreadable/unusable images instead of raising or crashing
    the pipeline - a corrupt/non-image file must come back as a clear, actionable state."""
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"this is not an image file")

    processed_path, quality = preprocess_image(image_path)

    assert quality.readable is False
    assert quality.issues
    assert processed_path == image_path


def test_missing_file_is_reported_as_not_readable(tmp_path: Path) -> None:
    image_path = tmp_path / "does-not-exist.png"

    _, quality = preprocess_image(image_path)

    assert quality.readable is False


def test_transparent_rgba_image_is_flattened_without_error(tmp_path: Path) -> None:
    """RGBA label art (e.g. a PNG exported with transparency) must be composited onto a
    background rather than crashing preprocessing or leaving an unusable alpha channel for
    OCR."""
    image_path = tmp_path / "label.png"
    Image.new("RGBA", (600, 600), (255, 0, 0, 128)).save(image_path)

    processed_path, quality = preprocess_image(image_path)

    assert quality.readable is True
    with Image.open(processed_path) as processed:
        assert "A" not in processed.getbands()


def test_processed_output_is_grayscale_for_ocr(tmp_path: Path) -> None:
    image_path = tmp_path / "label.png"
    Image.new("RGB", (600, 600), (10, 200, 60)).save(image_path)

    processed_path, _ = preprocess_image(image_path)

    with Image.open(processed_path) as processed:
        assert processed.mode == "L"
