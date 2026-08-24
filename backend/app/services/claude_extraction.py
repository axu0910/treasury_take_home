"""Claude-vision-based label field extraction - an opt-in extraction backend, used only when a
caller explicitly requests it (app.services.pipeline.verify_label's use_claude=True) and
ANTHROPIC_API_KEY is configured. Local Tesseract OCR (app.services.ocr /
app.services.extraction) is the default path regardless, since it's the only one that reliably
meets the ~5 second target in requirements.md section 3 - Claude vision measured ~10s average
and up to ~21s per real label photo in testing. app.services.pipeline also falls back to
Tesseract when Claude is requested without a key configured, or when a Claude call raises.

This reads the image and classifies its fields in a single model call rather than reusing the
OCR-then-regex two-step the local pipeline needs. That two-step split is exactly where the
local pipeline struggles: Tesseract's raw character recognition is fine on a clean, well-lit
photo, but deciding *which line is which field* - the brand vs. an address vs. marketing copy
vs. the warning - is a semantic judgment call regex/keyword heuristics are the wrong tool for
(see the extensive commentary and repeated iteration in app.services.extraction). A vision
model can make that judgment directly from the image instead of guessing from a flattened
word list.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from app.services.extraction import ExtractedFields

# Anthropic vision inputs are resized server-side past a certain resolution anyway, so sending
# a full 12-megapixel phone photo just inflates request size and latency for no legibility
# gain. Mirrors the same reasoning (and a similar bound) as app.services.ocr's local OCR cap.
_MAX_DIMENSION = 1600
_JPEG_QUALITY = 88


class ClaudeLabelExtraction(BaseModel):
    """Structured extraction contract for client.messages.parse(). Field descriptions are
    part of the actual prompt Claude sees (they become the JSON schema's field descriptions),
    not just documentation for humans reading this file."""

    image_readable: bool = Field(
        description="False only if the image is too blurry, dark, or cropped to read essentially any label text from at all."
    )
    quality_issues: list[str] = Field(
        default_factory=list,
        description=(
            "Short, specific notes on anything that limited how well you could read the label "
            "(e.g. 'glare across the warning text', 'label photographed at an angle', 'small "
            "back-label text is blurry'). Empty list if the photo is clean and well-composed."
        ),
    )
    confidence: float = Field(
        description=(
            "Your own confidence, from 0.0 to 1.0, that the fields below were read correctly "
            "and completely from this image. Use a low value for a genuinely hard read (a "
            "rotated bottle, a curved label, heavy glare) even if you still filled in most "
            "fields - confidence describes how much you'd trust this read, not how many "
            "fields are non-null."
        )
    )
    brand_name: str | None = Field(default=None, description="The brand name as printed on the label.")
    class_type: str | None = Field(
        default=None,
        description="The class/type designation, e.g. a spirit style ('Kentucky Straight Bourbon Whiskey') or a wine varietal ('Cabernet Sauvignon', 'Orange Muscat').",
    )
    alcohol_content: str | None = Field(default=None, description="Alcohol content / ABV as printed, e.g. '45% Alc./Vol.' or '13% BY VOL'.")
    net_contents: str | None = Field(default=None, description="Net contents / fill volume as printed, e.g. '750 mL'.")
    producer: str | None = Field(
        default=None,
        description="The bottler/producer/importer name and address, from a statement such as 'Bottled by', 'Distilled by', or 'Imported by'.",
    )
    country_of_origin: str | None = Field(default=None, description="Country of origin, generally only present for imported products.")
    government_warning: str | None = Field(
        default=None,
        description=(
            "The required U.S. government health warning statement, transcribed VERBATIM - "
            "character-for-character exactly as printed, including the exact wording, "
            "capitalization, and punctuation. Do NOT paraphrase, correct spelling, normalize "
            "casing, or clean up the wording in any way, even if it looks wrong: this text is "
            "compared against the legally required statement for an exact-match compliance "
            "check, so an unfaithful transcription defeats the entire purpose. If no such "
            "statement is visible anywhere on the label, use null - do not guess or "
            "reconstruct it from memory of what it is supposed to say."
        ),
    )
    raw_text: str = Field(
        default="",
        description="A verbatim transcription of every piece of text visible anywhere on the label, in reading order. Used as an audit trail, not just the structured fields above.",
    )


_SYSTEM_PROMPT = """You are assisting a U.S. Alcohol and Tobacco Tax and Trade Bureau (TTB) \
compliance agent by reading an alcohol beverage label photo and transcribing the fields \
required for label review.

Rules:
- Never fabricate a value. If a field is not clearly visible or not present on the label, \
return null for it rather than guessing.
- The government_warning field must be an exact, verbatim transcription - see its field \
description for why this matters. Every other field should be transcribed as printed, not \
paraphrased or corrected.
- Judge each line of text by its role on the label (brand vs. class/type vs. marketing copy \
vs. an address vs. the warning), not just by keyword matching - you are being asked for this \
precisely because you can make that judgment, not just detect words.
- Assess and report image quality honestly. A hard photo (glare, an odd angle, tiny text) \
should get a lower confidence score, not a best-effort guess presented as certain - an agent \
is relying on this signal to decide whether to trust the read or ask for a better photo."""


@dataclass(frozen=True)
class ClaudeQuality:
    readable: bool
    issues: list[str]
    confidence: float


def is_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)


def extract_with_claude(image_path: Path) -> tuple[ExtractedFields, str, ClaudeQuality]:
    """Read a label image directly with Claude vision and return the same ExtractedFields
    shape the local OCR pipeline produces, so app.services.pipeline can treat both extraction
    backends interchangeably. Raises on any API/image error - callers are expected to catch
    and fall back to the local OCR pipeline (see app.services.pipeline.verify_label)."""
    image_data = _encode_image(image_path)
    parsed = _call_claude(image_data)
    fields = ExtractedFields(
        brand_name=parsed.brand_name,
        class_type=parsed.class_type,
        alcohol_content=parsed.alcohol_content,
        net_contents=parsed.net_contents,
        producer=parsed.producer,
        country_of_origin=parsed.country_of_origin,
        government_warning=parsed.government_warning,
    )
    quality = ClaudeQuality(readable=parsed.image_readable, issues=parsed.quality_issues, confidence=parsed.confidence)
    return fields, parsed.raw_text, quality


def _call_claude(image_data: str) -> ClaudeLabelExtraction:
    """The actual network call, isolated from extract_with_claude's image handling and field
    mapping so tests can replace just this function instead of mocking the Anthropic SDK."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                    {"type": "text", "text": "Extract the TTB label fields from this alcohol beverage label photo."},
                ],
            }
        ],
        output_format=ClaudeLabelExtraction,
        # Reading a label into a fixed schema is bounded transcription/classification, not
        # open-ended reasoning - low effort caps how much (billed) thinking the model spends
        # per call without disabling thinking outright (which has its own failure modes, e.g.
        # a tool call leaking into visible text). This is the second latency lever after the
        # Sonnet 5 model switch (see config.py) - both aim at the ~5 second target in
        # requirements.md section 3.
        output_config={"effort": "low"},
    )

    parsed = response.parsed_output
    if parsed is None:
        # stop_reason was something other than a clean structured-output completion (e.g. a
        # refusal) - treat it the same as any other extraction failure so the caller falls
        # back to the local OCR pipeline instead of crashing on a None dereference.
        raise RuntimeError(f"Claude did not return structured output (stop_reason={response.stop_reason!r}).")
    return parsed


def _encode_image(image_path: Path) -> str:
    from PIL import Image, ImageOps

    with Image.open(image_path) as opened:
        # Phones commonly store EXIF orientation metadata rather than rotating pixel data
        # (see the identical fix and rationale in app.services.ocr.read_label) - without this,
        # Claude would be reading a sideways image.
        image = ImageOps.exif_transpose(opened)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        if max(image.size) > _MAX_DIMENSION:
            ratio = _MAX_DIMENSION / max(image.size)
            image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))))

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=_JPEG_QUALITY)
        return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")
