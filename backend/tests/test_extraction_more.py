"""Additional app.services.extraction coverage beyond the brand/warning/missing-field cases
in test_validation.py: net contents, country of origin, noisy ABV merges, and producer-line
variants, all driven by requirements.md 2.2 (required label fields)."""

from app.services.extraction import extract_fields
from app.services.ocr import OCRWord


def _line(words: list[tuple[str, float]], y: int, start_x: int = 0) -> list[OCRWord]:
    """Lay out words left-to-right on one OCR line at height y."""
    result = []
    x = start_x
    for text, confidence in words:
        width = max(len(text) * 8, 10)
        result.append(OCRWord(text, confidence, (x, y, width, 10)))
        x += width + 5
    return result


def test_net_contents_extracts_milliliters() -> None:
    words = _line([("750", 1.0), ("mL", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.net_contents == "750 mL"


def test_net_contents_extracts_fluid_ounces() -> None:
    words = _line([("12", 1.0), ("fl", 1.0), ("oz", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.net_contents == "12 fl oz"


def test_net_contents_extracts_when_glued_to_adjacent_text_with_no_space() -> None:
    """Regression test: a real RapidOCR read of a dense small-print label glued the net
    contents and ABV statements into one token with no separating space at all
    ("750mLALC.12.5% BYVOL") - the unit's trailing word-boundary requirement in _find_volume
    used to fail to match "mL" immediately followed by "ALC", silently dropping net_contents
    entirely even though the correct value was right there in the text."""
    words = _line([("750mLALC.12.5%", 0.7), ("BYVOL", 0.7)], y=0)

    extracted = extract_fields(words)

    assert extracted.net_contents == "750mL"


def test_net_contents_does_not_false_positive_on_a_genuine_word_after_the_unit() -> None:
    """The flip side of the fix above: a real word continuation right after what looks like a
    unit (not an OCR gluing artifact) must still not be treated as a match."""
    words = _line([("5Lemon", 1.0), ("flavored", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.net_contents is None


def test_country_of_origin_extracts_product_of_phrase() -> None:
    # "product of" is recognized as a field label by _find_field (checked before the
    # _find_origin regex fallback), so the label word itself is stripped and only the
    # country name is returned - matching the plain-country-name format an agent would type
    # into the application's countryOfOrigin field (e.g. "United States" in arch.md's sample
    # request contract).
    words = _line([("Product", 1.0), ("of", 1.0), ("France", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.country_of_origin == "France"


def test_country_of_origin_made_in_phrasing_is_not_recognized() -> None:
    """Common on imported labels ("Made in Scotland"), but _find_origin only matches the
    literal "product of" / "made of" wording, not "made in". This pins down the current gap
    so a label using this (very common) phrasing is documented as extracting no country
    rather than silently mis-parsing something else."""
    words = _line([("Made", 1.0), ("in", 1.0), ("Scotland", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.country_of_origin is None


def test_alcohol_content_extracts_simple_percentage() -> None:
    words = _line([("45%", 1.0), ("ALC/VOL", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.alcohol_content == "45% ALC/VOL"


def test_alcohol_content_recovers_from_noisy_merged_ocr_token() -> None:
    """Regression guard for the noisy-OCR fallback: a bottle-volume prefix and decimal ABV
    that OCR merges into one token (e.g. "SCL45%VOL") should still resolve to a usable ABV
    value instead of silently coming back empty."""
    words = _line([("SCL45%VOL", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.alcohol_content == "45% VOL"


def test_alcohol_content_out_of_range_percentage_is_rejected() -> None:
    """A percentage above 100 can't be a real ABV; extraction should not fabricate a field
    from an obvious OCR misread. Guards both the primary regex in _find_alcohol and the
    generic "abv"/"alc" label-search fallback, which previously had no numeric-range check
    and could return a garbage trailing fragment (e.g. "/VOL")."""
    words = _line([("450%", 1.0), ("ALC/VOL", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.alcohol_content is None


def test_net_contents_rejects_implausible_bottle_size() -> None:
    """Regression guard: a bare number-plus-unit match with no plausibility check could latch
    onto an unrelated number on the label (a lot code, a reference number) and report it as
    net contents. A 9 L bottle isn't a real standard-of-fill size for a single consumer
    container - reject it and keep looking, the same way alcohol_content already rejects an
    implausible percentage."""
    words = _line([("REF", 1.0), ("9L", 1.0), ("750", 1.0), ("mL", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.net_contents == "750 mL"


def test_producer_recognizes_imported_by_statement() -> None:
    """TTB requires an "Imported By" statement (not "bottled by"/"produced by") on imported
    alcohol labels - this was previously not recognized as a producer/bottler label at all, so
    a label with only an importer statement (no domestic bottler line) came back with
    producer=None even though the importer's name and address were right there in the OCR
    text."""
    words = (
        _line([("Imported", 1.0), ("By", 1.0)], y=0)
        + _line([("CS", 1.0), ("Distributors", 1.0), ("Co,", 1.0), ("New", 1.0), ("York", 1.0)], y=20)
    )

    extracted = extract_fields(words)

    assert extracted.producer is not None
    assert "Distributors" in extracted.producer


def test_warning_is_detected_even_when_ocr_drops_the_colon() -> None:
    """The colon after "WARNING" is a single thin character OCR drops surprisingly often. A
    warning that's otherwise present and legible should still be surfaced as evidence for the
    agent to review - it will still correctly fail the exact-text compliance check in
    validation.py either way, since a colon-less read can never equal the exact required
    statement."""
    words = _line([("GOVERNMENT", 1.0), ("WARNING", 1.0), ("This", 1.0), ("is", 1.0), ("a", 1.0), ("test", 1.0)], y=0)

    extracted = extract_fields(words)

    assert extracted.government_warning is not None
    assert extracted.government_warning.startswith("GOVERNMENT WARNING:")


def test_producer_extracted_after_distilled_and_bottled_by() -> None:
    words = (
        _line([("Distilled", 1.0), ("and", 1.0), ("Bottled", 1.0), ("by", 1.0)], y=0)
        + _line([("ABC", 1.0), ("Distillery,", 1.0), ("Frederick,", 1.0), ("MD", 1.0)], y=20)
    )

    extracted = extract_fields(words)

    assert extracted.producer is not None
    assert "Frederick" in extracted.producer


def test_class_type_falls_back_to_bare_keyword_when_no_full_line_candidate() -> None:
    """When no line is short/clean enough to be picked as a full class/type candidate, the
    generic single-keyword fallback should still recover a bare class designation."""
    words = _line(
        [
            ("This",1.0),("is",1.0),("a",1.0),("fine",1.0),("small",1.0),("batch",1.0),
            ("vodka",1.0),("produced",1.0),("in",1.0),("very",1.0),("small",1.0),
            ("quantities",1.0),("for",1.0),("connoisseurs",1.0),("everywhere",1.0),("today",1.0),
        ],
        y=0,
    )

    extracted = extract_fields(words)

    assert extracted.class_type == "VODKA"


def test_fields_absent_when_no_ocr_words_at_all() -> None:
    """An empty OCR result (e.g. a fully blank/unreadable crop) must not raise and must
    report every field as absent rather than guessing."""
    extracted = extract_fields([])

    assert extracted.brand_name is None
    assert extracted.alcohol_content is None
    assert extracted.net_contents is None
    assert extracted.producer is None
    assert extracted.country_of_origin is None
    assert extracted.government_warning is None


def test_warning_capture_excludes_trailing_label_text_after_the_sentence() -> None:
    """Regression for the QA fix in c6fc30d: capture must stop at the warning's own
    sentence-ending period, not run to the end of the OCR string."""
    from app.services.validation import EXPECTED_WARNING_TEXT

    words = _line(
        [(word, 1.0) for word in (EXPECTED_WARNING_TEXT + " CONTAINS SULFITES www.example.com").split()],
        y=0,
    )

    extracted = extract_fields(words)

    assert extracted.government_warning == EXPECTED_WARNING_TEXT
    assert "SULFITES" not in extracted.government_warning
