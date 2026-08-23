from app.services.extraction import ExtractedFields, extract_fields
from app.services.ocr import OCRWord
from app.services.validation import compare_fields, normalize_text


def test_normalize_text_ignores_case_and_repeated_whitespace() -> None:
    assert normalize_text("  Stone's   Throw ") == "stone's throw"


def test_warning_requires_exact_required_text_with_uppercase_prefix() -> None:
    valid_warning = (
        "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic "
        "beverages during pregnancy because of the risk of birth defects; (2) Consumption of alcoholic "
        "beverages impairs your ability to drive a car or operate machinery, and may cause health "
        "problems."
    )
    invalid_warning = (
        "Government Warning: (1) According to the Surgeon General, women should not drink alcoholic "
        "beverages during pregnancy because of the risk of birth defects; (2) Consumption of alcoholic "
        "beverages impairs your ability to drive a car or operate machinery, and may cause health "
        "problems."
    )

    valid_result = compare_fields(ExtractedFields(), ExtractedFields(government_warning=valid_warning))[-1]
    invalid_result = compare_fields(ExtractedFields(), ExtractedFields(government_warning=invalid_warning))[-1]

    assert valid_result.status == "match"
    assert invalid_result.status == "review"


def test_extraction_does_not_treat_warning_or_caption_text_as_fields() -> None:
    words = [
        OCRWord("ABC", 1.0, (0, 0, 20, 10)),
        OCRWord("DISTILLERY", 1.0, (25, 0, 80, 10)),
        OCRWord("DISTILLED", 1.0, (0, 20, 50, 10)),
        OCRWord("AND", 1.0, (55, 20, 25, 10)),
        OCRWord("BOTTLED", 1.0, (85, 20, 50, 10)),
        OCRWord("aN", 0.4, (140, 20, 15, 10)),
        OCRWord("ABC", 1.0, (0, 35, 20, 10)),
        OCRWord("DISTILLERY", 1.0, (25, 35, 80, 10)),
        OCRWord("FREDERICK,MD", 1.0, (110, 35, 90, 10)),
        OCRWord("45%", 1.0, (0, 50, 25, 10)),
        OCRWord("ALC/VOL", 1.0, (30, 50, 50, 10)),
        OCRWord("Brand", 1.0, (0, 65, 30, 10)),
        OCRWord("Label", 1.0, (35, 65, 30, 10)),
        OCRWord("Back", 1.0, (70, 65, 30, 10)),
        OCRWord("Label", 1.0, (105, 65, 30, 10)),
        OCRWord("alcoholic", 1.0, (0, 80, 50, 10)),
    ]

    extracted = extract_fields(words)

    assert extracted.brand_name == "ABC DISTILLERY"
    assert extracted.alcohol_content == "45% ALC/VOL"
    assert extracted.producer == "ABC DISTILLERY"


def test_extraction_classifies_winery_label_as_wine_and_cleans_brand() -> None:
    words = [
        OCRWord("Hawk’s", 1.0, (0, 0, 40, 10)),
        OCRWord("Shadow", 1.0, (45, 0, 60, 10)),
        OCRWord("Produced", 1.0, (110, 0, 55, 10)),
        OCRWord("and", 1.0, (170, 0, 25, 10)),
        OCRWord("Bottled", 1.0, (200, 0, 50, 10)),
        OCRWord("by", 1.0, (255, 0, 20, 10)),
        OCRWord("SS", 0.4, (280, 0, 20, 10)),
        OCRWord("Hawk’s", 1.0, (0, 20, 40, 10)),
        OCRWord("Shadow", 1.0, (45, 20, 60, 10)),
        OCRWord("Estate", 1.0, (110, 20, 45, 10)),
        OCRWord("Winery", 1.0, (160, 20, 50, 10)),
        OCRWord("Orange", 1.0, (0, 40, 45, 10)),
        OCRWord("Muscat", 1.0, (50, 40, 50, 10)),
    ]

    extracted = extract_fields(words)

    assert extracted.brand_name == "Hawk's Shadow"
    assert extracted.class_type == "WINE"
    # The same-line "by SS" remainder is too short to be a real value, so extraction
    # generically falls back to the next line rather than a brand-specific lookup.
    assert extracted.producer == "Hawk's Shadow Estate Winery"


def test_warning_detection_survives_ocr_truncating_prefix() -> None:
    words = [
        OCRWord("RNMENT", 0.5, (0, 0, 40, 10)),
        OCRWord("WARNING:", 1.0, (45, 0, 70, 10)),
    ]

    extracted = extract_fields(words)

    assert extracted.government_warning == "GOVERNMENT WARNING:"


def test_fields_absent_from_ocr_text_are_reported_as_missing_not_guessed() -> None:
    """Extraction must never fabricate a value it has no evidence for; fields the OCR
    text doesn't contain should come back as None so the comparison step routes them
    to manual review instead of silently passing."""
    words = [
        OCRWord("MASSETO", 1.0, (0, 0, 70, 10)),
        OCRWord("TOSCANA", 1.0, (0, 20, 70, 10)),
    ]

    extracted = extract_fields(words)

    assert extracted.brand_name == "MASSETO TOSCANA"
    assert extracted.alcohol_content is None
    assert extracted.net_contents is None
    assert extracted.producer is None
