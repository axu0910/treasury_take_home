from dataclasses import dataclass
import os
from pathlib import Path
import shutil

@dataclass(frozen=True)
class OCRWord:
    text: str
    confidence: float
    box: tuple[int, int, int, int]


def read_label(image_path: Path) -> tuple[list[OCRWord], str]:
    """Run local Tesseract and return text with confidence and evidence boxes."""
    import pytesseract
    from PIL import Image

    executable = _find_tesseract()
    if executable:
        pytesseract.pytesseract.tesseract_cmd = executable

    with Image.open(image_path) as image:
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
