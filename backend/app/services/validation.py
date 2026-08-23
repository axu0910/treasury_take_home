import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.services.extraction import ExtractedFields

EXPECTED_WARNING_PREFIX = "GOVERNMENT WARNING:"
EXPECTED_WARNING_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects; (2) Consumption of alcoholic "
    "beverages impairs your ability to drive a car or operate machinery, and may cause health "
    "problems."
)


@dataclass(frozen=True)
class FieldCheck:
    field: str
    status: str
    application_value: str | None
    label_value: str | None
    confidence: float
    reason: str | None = None


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def normalize_warning(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _brand_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def _alcohol_value(value: str) -> float | None:
    if not value:
        return None

    match = re.search(r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*%\s*(?:alc\.?\s*/\s*vol\.?|vol\.?|abv|alcohol)?", value, re.IGNORECASE)
    if match:
        parsed = float(match.group(1))
        if 0.0 < parsed <= 100.0:
            return parsed

    noisy_match = re.search(
        r"(?:scl|cl|alc|vol)\s*[:.]?\s*(\d{1,2}(?:[,.]\d)?)\s*%\s*(?:alc\.?\s*/\s*)?vol\.?",
        value,
        re.IGNORECASE,
    )
    if noisy_match:
        parsed = float(noisy_match.group(1).replace(",", "."))
        if 0.0 < parsed <= 100.0:
            return parsed

    return None


def compare_fields(application: ExtractedFields, label: ExtractedFields) -> list[FieldCheck]:
    """Compare normalized application values with extracted label values."""
    checks: list[FieldCheck] = []
    fields = (
        ("brand_name", application.brand_name, label.brand_name),
        ("class_type", application.class_type, label.class_type),
        ("alcohol_content", application.alcohol_content, label.alcohol_content),
        ("net_contents", application.net_contents, label.net_contents),
        ("producer", application.producer, label.producer),
        ("country_of_origin", application.country_of_origin, label.country_of_origin),
    )
    for field, application_value, label_value in fields:
        if not application_value and label_value:
            checks.append(
                FieldCheck(
                    field,
                    "review",
                    application_value,
                    label_value,
                    0.0,
                    "Application value was not provided; label value was detected.",
                )
            )
        elif application_value and not label_value:
            checks.append(
                FieldCheck(
                    field,
                    "missing",
                    application_value,
                    label_value,
                    0.0,
                    "Label value was not detected.",
                )
            )
        elif not application_value and not label_value:
            checks.append(
                FieldCheck(field, "missing", application_value, label_value, 0.0, "Neither value was provided or detected.")
            )
        elif field == "brand_name" and normalize_text(application_value) == normalize_text(label_value):
            checks.append(FieldCheck(field, "match", application_value, label_value, 1.0, "Case/whitespace-normalized match."))
        elif field == "brand_name" and _brand_similarity(application_value, label_value) >= 0.88:
            # Close but not identical even after normalization: this range also contains
            # genuine single-character OCR misreads (e.g. "Throw" vs "Throe"), which a pure
            # similarity score can't distinguish from a harmless formatting difference. Route
            # to review rather than silently auto-approving a name that isn't actually equal.
            similarity = _brand_similarity(application_value, label_value)
            checks.append(
                FieldCheck(
                    field,
                    "review",
                    application_value,
                    label_value,
                    similarity,
                    f"Brand names are similar ({similarity:.0%} match) but not identical after normalization; confirm this is the same brand before approving.",
                )
            )
        elif field == "alcohol_content":
            application_abv = _alcohol_value(application_value)
            label_abv = _alcohol_value(label_value)
            if application_abv is not None and label_abv is not None and abs(application_abv - label_abv) <= 0.5:
                checks.append(FieldCheck(field, "match", application_value, label_value, 0.95, "ABV values within 0.5 percentage points."))
            else:
                checks.append(FieldCheck(field, "mismatch", application_value, label_value, 0.95))
        elif normalize_text(application_value) == normalize_text(label_value):
            checks.append(FieldCheck(field, "match", application_value, label_value, 0.95))
        else:
            checks.append(FieldCheck(field, "mismatch", application_value, label_value, 0.95))

    warning = label.government_warning or ""
    prefix_is_uppercase = warning.startswith(EXPECTED_WARNING_PREFIX)
    warning_matches = normalize_warning(warning) == normalize_warning(EXPECTED_WARNING_TEXT)
    warning_is_valid = prefix_is_uppercase and warning_matches
    warning_reason = None
    if not prefix_is_uppercase:
        warning_reason = "The warning must begin with the exact uppercase prefix GOVERNMENT WARNING:."
    elif not warning_matches:
        warning_reason = "The warning text must match the required TTB statement exactly; altered or abbreviated wording is not acceptable."
    else:
        warning_reason = "Warning text matches exactly; confirm required bold formatting and placement visually."
    checks.append(
        FieldCheck(
            "government_warning",
            "match" if warning_is_valid else "review",
            EXPECTED_WARNING_PREFIX,
            label.government_warning,
            0.9 if warning_is_valid else 0.0,
            warning_reason,
        )
    )
    return checks
