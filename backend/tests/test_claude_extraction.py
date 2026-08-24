"""Coverage for app.services.claude_extraction. The actual network call (_call_claude) is
mocked throughout - these tests exercise image handling, field mapping, and the
is_configured()/error-propagation contract that app.services.pipeline relies on to decide
between the Claude and local-OCR paths, without making a real (paid, network-dependent) API
call. See test_pipeline.py for coverage of the fallback behavior this module's errors drive."""

from pathlib import Path

import pytest
from PIL import Image

from app.services import claude_extraction
from app.services.claude_extraction import ClaudeLabelExtraction


def _sample_image(tmp_path: Path, size: tuple[int, int] = (400, 300)) -> Path:
    path = tmp_path / "label.png"
    Image.new("RGB", size, "white").save(path)
    return path


def test_is_configured_reflects_api_key_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_extraction, "ANTHROPIC_API_KEY", None)
    assert claude_extraction.is_configured() is False

    monkeypatch.setattr(claude_extraction, "ANTHROPIC_API_KEY", "sk-test-key")
    assert claude_extraction.is_configured() is True


def test_extract_with_claude_maps_parsed_output_to_extracted_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = ClaudeLabelExtraction(
        image_readable=True,
        quality_issues=[],
        confidence=0.92,
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        producer="Example Distillery, Kentucky",
        country_of_origin="United States",
        government_warning="GOVERNMENT WARNING: ...",
        raw_text="OLD TOM DISTILLERY Kentucky Straight Bourbon Whiskey 750 mL 45% Alc./Vol.",
    )
    monkeypatch.setattr(claude_extraction, "_call_claude", lambda image_data: parsed)

    fields, raw_text, quality = claude_extraction.extract_with_claude(_sample_image(tmp_path))

    assert fields.brand_name == "OLD TOM DISTILLERY"
    assert fields.class_type == "Kentucky Straight Bourbon Whiskey"
    assert fields.alcohol_content == "45% Alc./Vol."
    assert fields.net_contents == "750 mL"
    assert fields.producer == "Example Distillery, Kentucky"
    assert fields.country_of_origin == "United States"
    assert fields.government_warning == "GOVERNMENT WARNING: ..."
    assert raw_text == parsed.raw_text
    assert quality.readable is True
    assert quality.issues == []
    assert quality.confidence == 0.92


def test_extract_with_claude_never_fabricates_fields_the_model_left_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors requirements.md 2.5's "never fabricate" principle, now enforced by the model's
    own instructions (see _SYSTEM_PROMPT) rather than a local regex - this test just confirms
    the mapping layer passes nulls through rather than substituting empty strings or guesses."""
    parsed = ClaudeLabelExtraction(
        image_readable=True,
        quality_issues=["label photographed at a steep angle"],
        confidence=0.4,
        brand_name="MASSETO",
        raw_text="MASSETO 2012",
    )
    monkeypatch.setattr(claude_extraction, "_call_claude", lambda image_data: parsed)

    fields, _, quality = claude_extraction.extract_with_claude(_sample_image(tmp_path))

    assert fields.brand_name == "MASSETO"
    assert fields.alcohol_content is None
    assert fields.net_contents is None
    assert fields.producer is None
    assert fields.country_of_origin is None
    assert fields.government_warning is None
    assert quality.confidence == 0.4
    assert quality.issues == ["label photographed at a steep angle"]


def test_extract_with_claude_propagates_call_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """app.services.pipeline relies on this raising (rather than returning a sentinel) to
    decide when to fall back to the local OCR pipeline - see test_pipeline.py."""

    def failing_call(image_data: str) -> ClaudeLabelExtraction:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(claude_extraction, "_call_claude", failing_call)

    with pytest.raises(RuntimeError, match="rate limited"):
        claude_extraction.extract_with_claude(_sample_image(tmp_path))


def test_encode_image_downscales_large_images(tmp_path: Path) -> None:
    import base64
    import io

    large_path = _sample_image(tmp_path, size=(4000, 3000))

    encoded = claude_extraction._encode_image(large_path)
    decoded = Image.open(io.BytesIO(base64.b64decode(encoded)))

    assert max(decoded.size) <= claude_extraction._MAX_DIMENSION
