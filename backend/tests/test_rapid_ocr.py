"""Coverage for app.services.rapid_ocr - the box-conversion/mapping logic and the
is_available() caching contract app.services.pipeline relies on. The real rapidocr_onnxruntime
package is an optional extra (see pyproject.toml's "rapidocr" group) that isn't installed in
this test environment - see conftest.py's _no_real_rapidocr_engine fixture for why every test
here works entirely against a fake stand-in module rather than the real dependency, the same
"mock the network/model boundary, not the app logic" approach test_claude_extraction.py uses
for the Anthropic SDK.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from app.services import rapid_ocr


def _sample_image(tmp_path: Path) -> Path:
    path = tmp_path / "label.png"
    Image.new("RGB", (400, 300), "white").save(path)
    return path


def test_is_available_reflects_whether_the_import_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rapid_ocr, "_import_failed", False)
    monkeypatch.delitem(sys.modules, "rapidocr_onnxruntime", raising=False)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", types.ModuleType("rapidocr_onnxruntime"))

    assert rapid_ocr.is_available() is True


def test_is_available_caches_a_failed_import_without_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    """app.services.pipeline calls is_available() on every verification - a real missing
    dependency shouldn't re-attempt (and re-fail) the import every single time."""
    monkeypatch.setattr(rapid_ocr, "_import_failed", False)
    monkeypatch.delitem(sys.modules, "rapidocr_onnxruntime", raising=False)
    # Block the import so it genuinely fails once, the same way it would with the extra not
    # installed at all.
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)

    assert rapid_ocr.is_available() is False
    assert rapid_ocr._import_failed is True
    # A second call must not touch sys.modules again - if it tried, it would still see None
    # and correctly return False either way, so this specifically guards the cached short-circuit.
    assert rapid_ocr.is_available() is False


def test_read_label_converts_quad_boxes_to_axis_aligned_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RapidOCR reports a rotated quadrilateral (4 corner points) per detected line; this must
    become the axis-aligned (left, top, width, height) box app.services.extraction expects."""

    class FakeEngine:
        def __call__(self, array):
            # A slightly skewed quad, and a low-confidence result that must still come through
            # unfiltered (filtering on confidence is app.services.pipeline's job, not this
            # module's).
            result = [
                ([[10, 20], [110, 18], [112, 50], [12, 52]], "GOVERNMENT WARNING:", 0.97),
                ([[10, 60], [90, 60], [90, 80], [10, 80]], "faint text", 0.12),
            ]
            return result, 0.05

    monkeypatch.setattr(rapid_ocr, "_engine", FakeEngine())

    words, raw_text = rapid_ocr.read_label(_sample_image(tmp_path))

    assert len(words) == 2
    assert words[0].text == "GOVERNMENT WARNING:"
    assert words[0].confidence == pytest.approx(0.97)
    left, top, width, height = words[0].box
    assert left == 10
    assert top == 18
    assert width == 102  # 112 - 10
    assert height == 34  # 52 - 18
    assert raw_text == "GOVERNMENT WARNING: faint text"


def test_read_label_drops_empty_text_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEngine:
        def __call__(self, array):
            return [([[0, 0], [10, 0], [10, 10], [0, 10]], "   ", 0.5)], 0.01

    monkeypatch.setattr(rapid_ocr, "_engine", FakeEngine())

    words, raw_text = rapid_ocr.read_label(_sample_image(tmp_path))

    assert words == []
    assert raw_text == ""


def test_read_label_propagates_engine_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """app.services.pipeline relies on this raising (rather than returning a sentinel) to fall
    back to a Tesseract-only read - see test_pipeline.py."""

    class FailingEngine:
        def __call__(self, array):
            raise RuntimeError("onnxruntime session crashed")

    monkeypatch.setattr(rapid_ocr, "_engine", FailingEngine())

    with pytest.raises(RuntimeError, match="onnxruntime session crashed"):
        rapid_ocr.read_label(_sample_image(tmp_path))
