from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps


@dataclass(frozen=True)
class ImageQuality:
    readable: bool
    issues: list[str]
    ocr_confidence: float = 0.0


def preprocess_image(image_path: Path) -> tuple[Path, ImageQuality]:
    """Create a normalized local image and report basic readability issues."""
    issues: list[str] = []
    processed_path = image_path.with_name(f"{image_path.stem}-processed.png")

    try:
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source)
            if "A" in image.getbands():
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image = ImageOps.grayscale(image)
            if min(image.size) < 300:
                issues.append("Image resolution may be too low for reliable OCR.")
            image = ImageOps.autocontrast(image)
            image = ImageEnhance.Contrast(image).enhance(1.15)
            image.save(processed_path, format="PNG")
    except (OSError, ValueError) as error:
        return image_path, ImageQuality(readable=False, issues=[f"Image could not be read: {error}"])

    return processed_path, ImageQuality(readable=True, issues=issues)
