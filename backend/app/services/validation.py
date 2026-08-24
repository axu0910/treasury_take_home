import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.services.extraction import ExtractedFields

# The two numbered sentences are separated by a period, not a semicolon - confirmed against
# the official 27 CFR 16.21 text and against every sample label in uploads/ that renders it
# (e.g. ttb_test.jpg, label1.png). An earlier version of this constant used a semicolon,
# which meant the exact-match check could never pass for a genuinely correctly-worded label.
EXPECTED_WARNING_PREFIX = "GOVERNMENT WARNING:"
EXPECTED_WARNING_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic "
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


def _text_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


# Claude vision is instructed to transcribe the producer field "from a statement such as
# 'Bottled by', 'Distilled by', or 'Imported by'" (see claude_extraction.py) and does so
# verbatim, boilerplate phrase included (e.g. "DISTILLED AND BOTTLED BY: ABC DISTILLERY,
# FREDERICK, MD"). The local OCR pipeline's producer field never includes this phrase in the
# first place - app.services.extraction._find_producer captures only the text *after* it. An
# application value an agent types is almost always just the name and address, with no
# statement phrase at all, so comparing the raw strings produces a spurious mismatch on an
# otherwise-identical producer purely because one side carries this label and the other
# doesn't. Stripping a recognized statement prefix before comparing (but keeping the original,
# unstripped value for display/evidence) fixes that without loosening the comparison on a
# genuinely different producer name or address - requirements.md 2.4 calls for producer/address
# differences to be "surfaced clearly", not normalized away.
_PRODUCER_STATEMENT_PREFIX = re.compile(
    r"^(?:distilled\s+and\s+bottled\s+by|bottled\s+and\s+distilled\s+by|distilled\s+by|"
    r"bottled\s+by|manufactured\s+by|produced\s+by|imported\s+by)\s*:?\s*",
    re.IGNORECASE,
)


def _strip_producer_statement_prefix(value: str) -> str:
    return _PRODUCER_STATEMENT_PREFIX.sub("", value.strip())


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


_VOLUME_CONVERSIONS_TO_ML = {
    "ml": 1.0,
    "cl": 10.0,
    "l": 1000.0,
    "oz": 29.5735295,
    "floz": 29.5735295,
}


def _volume_in_ml(value: str) -> float | None:
    """Parse a net-contents string into a canonical milliliter value so formatting
    differences (spacing, case, unit abbreviation) don't produce a false mismatch."""
    if not value:
        return None

    match = re.search(r"(\d{1,5}(?:\.\d+)?)\s*(ml|l|cl|fl\.?\s*oz\.?|floz|oz)\b", value, re.IGNORECASE)
    if not match:
        return None

    amount = float(match.group(1))
    unit = re.sub(r"[.\s]", "", match.group(2).lower())
    factor = _VOLUME_CONVERSIONS_TO_ML.get(unit)
    return amount * factor if factor is not None else None


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
        elif field == "brand_name" and _text_similarity(application_value, label_value) >= 0.88:
            # Close but not identical even after normalization: this range also contains
            # genuine single-character OCR misreads (e.g. "Throw" vs "Throe"), which a pure
            # similarity score can't distinguish from a harmless formatting difference. Route
            # to review rather than silently auto-approving a name that isn't actually equal.
            similarity = _text_similarity(application_value, label_value)
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
        elif field == "net_contents" and normalize_text(application_value) != normalize_text(label_value):
            # Fall back to a unit-normalized numeric comparison (e.g. "750mL" vs "750 mL", or
            # "0.75 L" vs "750 mL") before treating differently-formatted-but-equal values as
            # a mismatch. Text that already matches skips straight to the generic match branch
            # below, same as every other field.
            application_volume = _volume_in_ml(application_value)
            label_volume = _volume_in_ml(label_value)
            if application_volume is not None and label_volume is not None and abs(application_volume - label_volume) <= 1.0:
                checks.append(FieldCheck(field, "match", application_value, label_value, 0.95, "Net contents match after unit normalization."))
            else:
                checks.append(FieldCheck(field, "mismatch", application_value, label_value, 0.95))
        elif field == "producer" and normalize_text(application_value) != normalize_text(label_value):
            # See _strip_producer_statement_prefix above - only reached once the plain
            # normalized-text comparison below has already failed, same "try the strict
            # comparison first" order net_contents uses just above.
            application_producer = _strip_producer_statement_prefix(application_value)
            label_producer = _strip_producer_statement_prefix(label_value)
            if normalize_text(application_producer) == normalize_text(label_producer):
                checks.append(
                    FieldCheck(
                        field,
                        "match",
                        application_value,
                        label_value,
                        0.95,
                        "Producer matches after removing the bottler/distiller/importer statement prefix.",
                    )
                )
            elif _text_similarity(application_producer, label_producer) >= 0.88:
                # Beyond the statement prefix, real transcriptions also vary in punctuation a
                # human wouldn't (e.g. a vision model omitting the comma between a distillery
                # name and its city: "ABC DISTILLERY FREDERICK, MD" vs "ABC Distillery,
                # Frederick, MD") - the same near-miss situation brand_name already routes to
                # review rather than silently matching or hard-failing on punctuation alone.
                similarity = _text_similarity(application_producer, label_producer)
                checks.append(
                    FieldCheck(
                        field,
                        "review",
                        application_value,
                        label_value,
                        similarity,
                        f"Producers are similar ({similarity:.0%} match) but not identical after normalization; confirm this is the same producer before approving.",
                    )
                )
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
