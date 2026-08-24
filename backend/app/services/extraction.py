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
    # The colon after "WARNING" is optional here (detection only, not the exact-text compliance
    # check below in validation.py, which is unaffected and stays strict) - it's a single thin
    # character that OCR drops surprisingly often, and requiring it meant a warning that was
    # otherwise genuinely present and legible came back as no evidence at all (government_warning
    # = None) rather than the actual, still-clearly-non-compliant text an agent could review.
    warning_match = re.search(r"(?:GOVERNMENT|[A-Z]{0,5}RNMENT|[A-Z]{0,5}ENT)\s+WARNING\s*:?", raw_text, re.IGNORECASE)
    if warning_match:
        warning = raw_text[warning_match.start() :].strip()
        if not warning.upper().startswith("GOVERNMENT WARNING:"):
            warning = "GOVERNMENT WARNING:" + warning[warning_match.end() - warning_match.start() :]
        # The required warning is two numbered sentences ending "...may cause health
        # problems." - critically, clause (1) itself ends in its own period ("...birth
        # defects."), so bounding the capture at the *first* period (as an earlier version of
        # this code did) truncates the warning right after clause (1) and silently discards
        # clause (2) on every genuinely correct label. Bound the capture to the warning's own
        # closing phrase instead, so unrelated OCR text after the warning region on the label
        # (a sulfite declaration, a barcode, a website) still doesn't get folded into the
        # extracted value.
        closing_match = re.search(r"health\s+problems\.?", warning, re.IGNORECASE)
        if closing_match:
            warning = warning[: closing_match.end()]
        else:
            # An altered/abbreviated warning that never reaches the expected closing phrase -
            # fall back to the first period after clause (1)'s own length (155 characters),
            # so we still avoid capturing unrelated trailing text indefinitely without
            # truncating at the warning's internal (1)/(2) sentence boundary.
            sentence_end = warning.find(".", 160)
            if sentence_end != -1:
                warning = warning[: sentence_end + 1]
    else:
        warning = None

    brand_name = _find_brand_field(lines) or _infer_brand(lines)
    alcohol_content = _find_alcohol(text) or _valid_alcohol_fallback(_find_field(lines, ("abv", "alc", "alcohol", "% alc")))

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
                # TTB requires an "Imported By" statement (with the US importer's name and
                # address) on imported alcohol labels in place of a domestic bottler statement
                # - a real, common case this list was missing entirely, so a fully-legible
                # importer statement (e.g. "Imported By CS Distributors Co, New York - NY")
                # fell through to producer=None instead of being recognized at all.
                "imported by",
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


def _valid_alcohol_fallback(value: str | None) -> str | None:
    """The generic label-search fallback (_find_field for "abv"/"alc"/"alcohol") returns
    whatever text follows the label word with no numeric-range check, so a bogus or
    out-of-range OCR read (e.g. "450% ALC/VOL" leaving a trailing "/VOL" fragment) can end up
    as the extracted value. Only accept the fallback when it actually contains a plausible
    percentage, same as the primary regex in _find_alcohol already requires."""
    import re

    if not value:
        return None
    match = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", value)
    if not match:
        return None
    parsed = float(match.group(1))
    return value if 0.0 < parsed <= 100.0 else None


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


_ML_PER_UNIT = {"ml": 1.0, "l": 1000.0, "oz": 29.5735295, "floz": 29.5735295}
# TTB standards of fill for wine/spirits run from miniatures (50 mL) up to large-format bottles
# (a few products up to ~3-4 L); nothing sold as a single consumer bottle is two or three times
# that. Without this bound, a bare number-plus-unit match (e.g. a stray reference/lot code like
# "REF 9L" misread near the label) could produce an implausible value like "9 L" with no check
# at all - the same class of problem alcohol_content already guards against for ABV.
_MIN_PLAUSIBLE_ML = 30.0
_MAX_PLAUSIBLE_ML = 5000.0


def _find_volume(text: str) -> str | None:
    import re

    # No trailing \b after the unit: a small-print net-contents statement is often printed
    # immediately adjacent to the ABV statement with no separating space (e.g. "750mLALC.
    # 12.5% BYVOL", a real RapidOCR read where the underlying label print itself has minimal
    # kerning) - \b requires a transition between a word and non-word character, which a
    # directly-glued uppercase continuation like "mLALC" never has, so the match silently
    # failed and net_contents came back None even though the correct value was right there.
    # The lookahead only blocks a following *lowercase* letter (case-sensitive even though the
    # rest of the pattern is case-insensitive, via the scoped (?-i:) flag) so a genuine word
    # continuation like "5Lemon" still doesn't false-positive as "5 L".
    for match in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(mL|ML|L|oz|fl\.?\s*oz)(?!(?-i:[a-z]))", text, re.IGNORECASE):
        amount = float(match.group(1))
        unit = re.sub(r"[.\s]", "", match.group(2).lower())
        factor = _ML_PER_UNIT.get(unit)
        if factor is not None and _MIN_PLAUSIBLE_ML <= amount * factor <= _MAX_PLAUSIBLE_ML:
            return match.group(0)
    return None


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

    is_wine = bool(re.search(r"\b(?:wine|winery|wines)\b|sauvignon\s+blanc", text, re.IGNORECASE))

    # Look for an actual class/type designation line (a varietal like "Orange Muscat" or a
    # spirit style like "Straight Rye Whisky") before ever falling back to the bare beverage
    # category. This used to be checked only for spirits - wine labels short-circuited to a
    # generic "WINE" immediately above, which discarded a more specific, genuinely-detected
    # designation whenever one was sitting right there in the OCR text (e.g. this function
    # would return "WINE" for a label that also contains the line "Orange Muscat").
    #
    # SPECIFIC_KEYWORDS are checked first and always preferred over a bare beverage-category
    # line - each one is itself a real class/type designation. Common wine varietals are
    # included here (not just spirit styles) since a varietal name is exactly as legitimate a
    # TTB class/type entry as a spirit style is (e.g. "Cabernet Sauvignon", not just "WINE").
    specific_keywords = (
        "whiskey", "whisky", "bourbon", "chardonnay", "muscat", "cabernet", "sauvignon",
        "merlot", "pinot", "riesling", "zinfandel", "syrah", "shiraz", "malbec", "sangiovese",
        "moscato", "grenache", "tempranillo", "nebbiolo", "barbera", "viognier", "chianti",
    )
    keywords = specific_keywords + ("vodka", "gin", "rum", "tequila", "wine", "beer", "ale")
    candidates = [
        line.strip()
        for line in lines
        if len(line.strip()) <= 80
        and any(keyword in line.casefold() for keyword in keywords)
        # A website/social handle is the single most common source of a false-positive keyword
        # match here (e.g. "kimcrawfordwines.com" contains "wine"), and it's never itself a
        # class/type designation, so exclude it outright rather than let it win by being the
        # first or only match.
        and not re.search(r"www\.|\.com\b|https?://|@", line, re.IGNORECASE)
    ]
    # Prefer the shortest matching line, not just the first one in reading order. A real
    # class/type designation is a short, title-like phrase ("Orange Muscat", "Summation Red
    # Wine Blend"); a full descriptive sentence that happens to contain "wine" as a substring
    # ("This Tuscan wine is aged for...") is a real but much less useful match, and reading
    # order alone has no way to prefer one over the other.
    specific_candidates = [line for line in candidates if any(keyword in line.casefold() for keyword in specific_keywords)]
    selected = min(specific_candidates, key=len) if specific_candidates else (min(candidates, key=len) if candidates else None)
    if selected:
        selected = re.sub(r"^\d{4}\s+", "", selected)
    if selected:
        return selected

    if is_wine:
        return "WINE"

    match = re.search(r"\b(whisk(?:e)y|bourbon|vodka|gin|rum|tequila|wine|beer|ale)\b", text, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _find_producer(text: str, lines: list[str] | None = None) -> str | None:
    import re

    match = re.search(
        r"(?:produced|distilled|bottled|manufactured|imported)(?:\s+and\s+(?:bottled|distilled))?\s+(?:by\b|[a-z]{1,3}\b)\s*:?(.*?)(?=\s+(?:[A-Z][A-Za-z]+\s*,\s*[A-Z]{2}\b|[A-Z][A-Za-z]+\s+\d{5}\b|https?://|$))",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match and lines:
        for index, line in enumerate(lines):
            if re.search(r"bottled\s+by|imported\s+by", line, re.IGNORECASE) and index + 1 < len(lines):
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
