"""RapidOCR (ONNXRuntime) local text recognition - the primary engine behind most fields of
the default local extraction path, used alongside Tesseract rather than in place of it. See
app.services.pipeline._verify_with_local_ocr for how the two are combined.

Why both engines instead of one: measured on real label photos, RapidOCR's deep-learning
recognizer reads normal print meaningfully more accurately than Tesseract's classical engine
(see the comparison in app.services.pipeline's docstring/comments), which is why it drives
brand_name, class_type, alcohol_content, net_contents, producer, and country_of_origin. But it
also has a real failure mode on small, dense print - it frequently drops the spaces between
words entirely (e.g. "GOVERNMENT WARNING:ACCORDINGTOTHESURGEONGENERAL...") rather than merely
misreading a character, and that specific print size is exactly where the government warning
statement is printed. Since requirement 2.3 treats the warning as an exact-text compliance
check, a glued-together read can never pass it even when every character is correct - there's
no whitespace left to normalize away. Tesseract's word-level bounding boxes don't share this
failure mode, so the warning field specifically still comes from Tesseract's read
(app.services.ocr.read_label), not this module.

This module is an optional dependency from the app's perspective (see the "rapidocr" extra in
pyproject.toml and is_available() below): the app must keep working with Tesseract alone when
rapidocr_onnxruntime isn't installed (its rapidocr-onnxruntime dependency currently caps out at
Python <3.13, so a newer local dev interpreter - or any environment where the extra wasn't
installed - simply won't have it).
"""

from __future__ import annotations

from pathlib import Path

from app.services.ocr import OCRWord

_engine = None  # lazily constructed - onnxruntime session init has real cost that should only
                 # be paid the first time this path is actually used.
_import_failed = False  # cached after the first failed import so repeated calls don't
                         # re-attempt (and re-fail) a slow import on every verification.


def is_available() -> bool:
    global _import_failed
    if _import_failed:
        return False
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        _import_failed = True
        return False
    return True


def read_label(image_path: Path) -> tuple[list[OCRWord], str]:
    """Run RapidOCR and return the same (words, raw_text) shape app.services.ocr.read_label
    produces, so app.services.extraction.extract_fields can treat either engine's output
    identically. Raises on any import/model/runtime error - the caller
    (app.services.pipeline._verify_with_local_ocr) is expected to catch and fall back to a
    Tesseract-only read, the same contract app.services.claude_extraction uses for its own
    fallback."""
    import numpy as np
    from PIL import Image, ImageOps

    engine = _get_engine()

    with Image.open(image_path) as opened:
        # Phones commonly store EXIF orientation metadata rather than rotating pixel data (see
        # the identical fix in app.services.ocr.read_label and app.services.claude_extraction) -
        # without this, RapidOCR reads a sideways image.
        image = ImageOps.exif_transpose(opened)
        if image.mode != "RGB":
            image = image.convert("RGB")
        array = np.array(image)

    result, _elapse = engine(array)
    words = [word for word in (_to_word(box, text, score) for box, text, score in (result or [])) if word.text]
    return words, " ".join(word.text for word in words)


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def _to_word(box, text: str, score: float) -> OCRWord:
    # box is a quadrilateral (4 [x, y] corner points) since RapidOCR's detector supports
    # rotated text; app.services.extraction only needs an axis-aligned box to group words into
    # lines by vertical position, so the bounding rectangle of the quad is sufficient here. Each
    # RapidOCR result is already a full recognized line/phrase rather than a single word, but
    # that's fine for the same reason - _group_lines groups by y-position, and a pre-grouped
    # line just becomes a group of one.
    xs = [point[0] for point in box]
    ys = [point[1] for point in box]
    left, top = min(xs), min(ys)
    width, height = max(xs) - left, max(ys) - top
    return OCRWord(text=text.strip(), confidence=float(score), box=(int(left), int(top), int(width), int(height)))
