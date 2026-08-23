from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.ocr import OCRWord


@dataclass(frozen=True)
class ExtractedFields:
    brand_name: str | None = None
    class_type: str | None = None
    alcohol_content: str | None = None
    net_contents: str | None = None
    producer: str | None = None
    country_of_origin: str | None = None
    government_warning: str | None = None


def extract_fields(words: list["OCRWord"]) -> ExtractedFields:
    import re

    """Map OCR words and layout into the fields required by the review workflow."""
    lines = _group_lines(words)
    text = " ".join(lines)
    raw_text = " ".join(word.text for word in words)
    warning_match = re.search(r"(?:GOVERNMENT|[A-Z]{0,5}RNMENT|[A-Z]{0,5}ENT)\s+WARNING\s*:", raw_text, re.IGNORECASE)
    if warning_match:
        warning = raw_text[warning_match.start() :].strip()
        if not warning.upper().startswith("GOVERNMENT WARNING:"):
            warning = "GOVERNMENT WARNING:" + warning[warning_match.end() - warning_match.start() :]
        # The required warning is a single sentence ending in one period. Bound the capture
        # to that sentence so unrelated OCR text after the warning region on the label (a
        # sulfite declaration, a barcode, a website) doesn't get folded into the extracted
        # value and cause an otherwise letter-perfect warning to fail the exact-match check.
        sentence_end = warning.find(".")
        if sentence_end != -1:
            warning = warning[: sentence_end + 1]
    else:
        warning = None

    brand_name = _find_brand_field(lines) or _infer_brand(lines)
    alcohol_content = _find_alcohol(text) or _find_field(lines, ("abv", "alc", "alcohol", "% alc"))

    producer = _normalize_producer(
        _find_field(
            lines,
            (
                "producer",
                "bottled by",
                "distilled by",
                "distilled and bottled by",
                "bottled and distilled by",
                "manufactured by",
            ),
        ),
        text,
    ) or _find_producer(text, lines)

    return ExtractedFields(
        brand_name=brand_name,
        class_type=_find_field(lines, ("class/type", "class", "type")) or _infer_class_type(lines, text),
        alcohol_content=alcohol_content,
        net_contents=_find_field(lines, ("net contents", "contents", "volume")) or _find_volume(text),
        producer=producer,
        country_of_origin=_find_field(lines, ("country of origin", "product of")) or _find_origin(text),
        government_warning=warning,
    )


def _group_lines(words: list["OCRWord"]) -> list[str]:
    grouped: list[tuple[int, list["OCRWord"]]] = []
    for word in sorted(words, key=lambda item: (item.box[1], item.box[0])):
        center_y = word.box[1] + word.box[3] // 2
        target = next((line for line in grouped if abs(line[0] - center_y) <= max(word.box[3], 12)), None)
        if target is None:
            grouped.append((center_y, [word]))
        else:
            target[1].append(word)
    return [" ".join(word.text for word in sorted(line, key=lambda item: item.box[0])) for _, line in grouped]


def _find_field(lines: list[str], labels: tuple[str, ...]) -> str | None:
    import re

    for index, line in enumerate(lines):
        lowered = line.casefold().strip()
        for label in labels:
            label_lower = label.casefold()
            match = re.search(rf"(?<![a-z0-9]){re.escape(label_lower)}(?![a-z0-9])", lowered)
            if match:
                start = match.start()
                value = line[start + len(label) :].lstrip(" :.-")
                # A one- or two-character remainder on the label's own line is almost always
                # OCR noise (a stray character or a mis-split preposition) rather than a real
                # value, so treat it the same as an empty match and fall back to the next line.
                if len(value.strip()) >= 3:
                    return value
                return lines[index + 1].strip() if index + 1 < len(lines) else None
    return None


def _find_brand_field(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        lowered = line.casefold().strip()
        if not lowered.startswith(("brand:", "brand name:", "brand name ")):
            continue
        value = line.split(":", 1)[1].strip() if ":" in line else line[len("brand name") :].strip()
        if value and value.casefold() not in {"label", "back label"}:
            return value
        if index + 1 < len(lines):
            return lines[index + 1].strip()
    return None


def _normalize_producer(value: str | None, text: str) -> str | None:
    if not value:
        return None
    return _clean_repeated_prefix(value)


def _find_alcohol(text: str) -> str | None:
    import re

    match = re.search(r"\b\d{1,2}(?:\.\d+)?\s*%\s*(?:alc\.?\s*/\s*vol\.?)?", text, re.IGNORECASE)
    if match:
        value = float(match.group(0).split('%', 1)[0].strip())
        if 0.0 < value <= 100.0:
            return match.group(0).strip()

    # OCR can merge the bottle-volume prefix and decimal ABV, e.g. SCL45%VOL.
    noisy_match = re.search(
        r"(?:scl|cl|alc|vol)\s*[:.]?\s*(\d{1,2})(?:[,.](\d))?\s*%\s*(?:alc\.?\s*/\s*)?vol\.?",
        text,
        re.IGNORECASE,
    )
    if not noisy_match:
        return None
    whole, decimal = noisy_match.groups()
    value = float(f"{whole}.{decimal}") if decimal else float(whole)
    if 0.0 < value <= 100.0:
        return f"{value:g}% VOL"
    return None


def _find_volume(text: str) -> str | None:
    import re

    match = re.search(r"\b\d+(?:\.\d+)?\s*(?:mL|ML|L|oz|fl\.?\s*oz)\b", text, re.IGNORECASE)
    return match.group(0) if match else None


def _find_origin(text: str) -> str | None:
    import re

    match = re.search(r"\b(?:product|made)\s+of\s+([A-Za-z][A-Za-z ]+)", text, re.IGNORECASE)
    return f"Product of {match.group(1).strip()}" if match else None


def _infer_brand(lines: list[str]) -> str | None:
    if len(lines) >= 2:
        first, second = lines[0].strip(), lines[1].strip()
        if (
            first
            and second
            and len(first) <= 30
            and len(second) <= 30
            and first.upper() == first
            and second.upper() == second
            and not _looks_like_metadata(first)
            and not _looks_like_metadata(second)
        ):
            return _clean_repeated_prefix(f"{first} {second}")
    for line in lines[:3]:
        candidate = line.strip()
        if candidate and candidate.upper() == candidate and not _looks_like_metadata(candidate):
            return _clean_repeated_prefix(candidate)
    return _clean_repeated_prefix(lines[0].strip()) if lines else None


def _infer_class_type(lines: list[str], text: str) -> str | None:
    import re

    if re.search(r"\b(?:wine|winery|wines)\b|sauvignon\s+blanc", text, re.IGNORECASE):
        return "WINE"
    keywords = ("whiskey", "whisky", "bourbon", "vodka", "gin", "rum", "tequila", "chardonnay", "muscat", "wine", "beer", "ale")
    candidates = [
        line.strip()
        for line in lines
        if len(line.strip()) <= 80 and any(keyword in line.casefold() for keyword in keywords)
    ]
    selected = next(
        (line for line in candidates if any(keyword in line.casefold() for keyword in ("whiskey", "whisky", "bourbon", "chardonnay", "muscat"))),
        None,
    ) or (candidates[0] if candidates else None)
    if selected:
        selected = re.sub(r"^\d{4}\s+", "", selected)
    if selected:
        return selected
    match = re.search(r"\b(whisk(?:e)y|bourbon|vodka|gin|rum|tequila|wine|beer|ale)\b", text, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _find_producer(text: str, lines: list[str] | None = None) -> str | None:
    import re

    match = re.search(
        r"(?:produced|distilled|bottled|manufactured)(?:\s+and\s+(?:bottled|distilled))?\s+(?:by\b|[a-z]{1,3}\b)\s*:?(.*?)(?=\s+(?:[A-Z][A-Za-z]+\s*,\s*[A-Z]{2}\b|[A-Z][A-Za-z]+\s+\d{5}\b|https?://|$))",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match and lines:
        for index, line in enumerate(lines):
            if re.search(r"bottled\s+by", line, re.IGNORECASE) and index + 1 < len(lines):
                return _clean_repeated_prefix(lines[index + 1].strip()) or None
    if not match:
        return None
    value = match.group(1).strip(" \n\t.-,:")
    return _clean_repeated_prefix(value) or None


def _clean_repeated_prefix(value: str) -> str:
    import re

    value = value.replace("’", "'")
    value = re.sub(r"\s+produced\s+and\s+bottled\s+by.*$", "", value, flags=re.IGNORECASE)
    tokens = value.split()
    if len(tokens) > 1 and tokens[0].casefold() == tokens[1].casefold():
        return " ".join(tokens[1:])
    return value


def _looks_like_metadata(value: str) -> bool:
    metadata = ("GOVERNMENT", "WARNING", "ALCOHOL", "CONTENTS", "PRODUCT", "MADE IN")
    return any(value.startswith(prefix) for prefix in metadata)


def _find_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    lowered = text.casefold()
    for label in labels:
        marker = f"{label.casefold()}:"
        start = lowered.find(marker)
        if start >= 0:
            value = text[start + len(marker):].strip().split("  ", 1)[0]
            return value or None
    return None
