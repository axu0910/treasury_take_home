from dataclasses import dataclass
import os
from pathlib import Path
import shutil

@dataclass(frozen=True)
class OCRWord:
    text: str
    confidence: float
    box: tuple[int, int, int, int]


# Bound to keep multi-pass OCR cost roughly constant regardless of source resolution. A phone
# photo commonly comes in at 12+ megapixels (e.g. 4032x3024); printed label text doesn't get
# more legible to Tesseract past a couple thousand pixels on the long edge, but every pass
# below (2 full-image reads plus up to two upscaled-crop passes) scales with pixel count, so a
# 12MP source was taking 10+ seconds end to end. Downscaling once here bounds every pass
# without touching the preprocessed artifact written to disk. Boxes returned by this function
# are in the coordinate space of the (possibly downscaled) working image, which is fine since
# nothing outside a single read_label() call depends on them lining up with the original file.
_MAX_OCR_DIMENSION = 2000


def read_label(image_path: Path) -> tuple[list[OCRWord], str]:
    """Run local Tesseract and return text with confidence and evidence boxes."""
    import pytesseract
    from PIL import Image, ImageOps

    executable = _find_tesseract()
    if executable:
        pytesseract.pytesseract.tesseract_cmd = executable

    with Image.open(image_path) as opened:
        # Phone cameras commonly store the sensor's native (often landscape) orientation and
        # record the intended display rotation as EXIF metadata instead of rotating the pixel
        # data - e.g. a portrait phone photo saved as 4032x3024 pixels with a 90-degree EXIF
        # orientation tag. preprocess_image already corrects this for the preprocessed
        # candidate, but this function is also called directly on the *original* file as a
        # fallback candidate (see pipeline.py), which needs the same correction - without it,
        # Tesseract reads a sideways image and both text and every crop-region heuristic below
        # (which assume the label is right-side up) produce poor results.
        image = ImageOps.exif_transpose(opened)
        # pytesseract only accepts a fixed allowlist of PIL format strings (PNG, JPEG, ...)
        # and raises TypeError('Unsupported image format/type') for anything else. Some real
        # phone photos - notably iPhone photos taken in Portrait mode - decode fine in Pillow
        # but report format "MPO" (Multi-Picture Object), which isn't on that allowlist and
        # would otherwise crash OCR outright. Clearing the format makes pytesseract fall back
        # to re-encoding the in-memory pixel data as PNG, which works regardless of the
        # original file format.
        image.format = None
        if max(image.size) > _MAX_OCR_DIMENSION:
            ratio = _MAX_OCR_DIMENSION / max(image.size)
            image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))))

        candidates = [
            _words_from_data(pytesseract.image_to_data(image, config=f"--psm {psm}", output_type=pytesseract.Output.DICT))
            for psm in (3, 11)
        ]
        if image.height >= 800:
            crop = image.crop((0, int(image.height * 0.4), image.width, int(image.height * 0.78)))
            crop = crop.resize((crop.width * 3, crop.height * 3))
            for psm in (6, 11):
                crop_words = _words_from_data(
                    pytesseract.image_to_data(crop, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
                )
                candidates.append(_offset_words(crop_words, 0, int(image.height * 0.4), 3))
        if image.height >= image.width * 1.5:
            left = int(image.width * 0.25)
            right = int(image.width * 0.78)
            top = int(image.height * 0.58)
            bottom = int(image.height * 0.95)
            crop = image.crop((left, top, right, bottom)).resize(((right - left) * 2, (bottom - top) * 2))
            crop_words = _words_from_data(
                pytesseract.image_to_data(crop, config="--psm 11", output_type=pytesseract.Output.DICT)
            )
            candidates.append(_offset_words(crop_words, left, top, 2))

    words = max(candidates, key=_candidate_quality)
    return words, " ".join(word.text for word in words)


def _offset_words(words: list[OCRWord], offset_x: int, offset_y: int, scale: int) -> list[OCRWord]:
    return [
        OCRWord(
            word.text,
            word.confidence,
            (word.box[0] // scale + offset_x, word.box[1] // scale + offset_y, word.box[2] // scale, word.box[3] // scale),
        )
        for word in words
    ]


def _words_from_data(data) -> list[OCRWord]:
    words: list[OCRWord] = []

    for index, text in enumerate(data["text"]):
        text = text.strip()
        if not text:
            continue
        confidence = max(float(data["conf"][index]), 0.0) / 100
        words.append(
            OCRWord(
                text=text,
                confidence=confidence,
                box=(
                    int(data["left"][index]),
                    int(data["top"][index]),
                    int(data["width"][index]),
                    int(data["height"][index]),
                ),
            )
        )

    return words


def _candidate_quality(words: list[OCRWord]) -> tuple[int, float]:
    from app.services.extraction import extract_fields

    extracted = extract_fields(words)
    field_count = sum(value is not None for value in (
        extracted.brand_name,
        extracted.class_type,
        extracted.alcohol_content,
        extracted.net_contents,
        extracted.producer,
        extracted.country_of_origin,
        extracted.government_warning,
    ))
    confidence = sum(word.confidence for word in words) / len(words) if words else 0.0
    return field_count, confidence


def _find_tesseract() -> str | None:
    configured = os.environ.get("TESSERACT_CMD")
    if configured and Path(configured).is_file():
        return configured

    candidates = (
        shutil.which("tesseract"),
        "/opt/homebrew/opt/tesseract/bin/tesseract",
        "/usr/local/opt/tesseract/bin/tesseract",
    )
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)
