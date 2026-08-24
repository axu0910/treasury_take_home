"""Edge-case coverage for app.services.validation.compare_fields, driven directly by
requirements.md section 2.4 (Field comparison) and 2.3 (Government warning validation).

test_validation.py already covers the warning exact-match/uppercase-prefix contract and the
brand normalization happy path; this file rounds out the comparison matrix: missing-value
routing, the brand near-miss regression, ABV tolerance boundaries, and net-contents/country
formatting behavior.
"""

from app.services.extraction import ExtractedFields
from app.services.validation import compare_fields


def _check(checks, field: str):
    return next(check for check in checks if check.field == field)


def test_application_value_missing_but_label_detected_routes_to_review() -> None:
    """Requirement 2.4: ambiguous/incomplete comparisons must be routed to manual review,
    not silently matched or silently failed."""
    application = ExtractedFields(brand_name=None)
    label = ExtractedFields(brand_name="OLD TOM DISTILLERY")

    check = _check(compare_fields(application, label), "brand_name")

    assert check.status == "review"
    assert check.confidence == 0.0


def test_label_value_not_detected_is_reported_missing() -> None:
    """Requirement 2.5: low-confidence/absent extraction must not be treated as an automatic
    pass; a field the label never yielded is reported as missing evidence."""
    application = ExtractedFields(net_contents="750 mL")
    label = ExtractedFields(net_contents=None)

    check = _check(compare_fields(application, label), "net_contents")

    assert check.status == "missing"


def test_both_values_absent_is_reported_missing_not_matched() -> None:
    application = ExtractedFields(country_of_origin=None)
    label = ExtractedFields(country_of_origin=None)

    check = _check(compare_fields(application, label), "country_of_origin")

    assert check.status == "missing"
    assert check.reason == "Neither value was provided or detected."


def test_brand_near_miss_similarity_routes_to_review_not_auto_pass() -> None:
    """Regression for the QA fix in c6fc30d: a genuine single-character OCR misread
    ("Throw" vs "Throe") lands at ~92% similarity, inside the 0.88-1.0 band. That band must
    route to review rather than auto-match, because a pure similarity score can't tell a
    misread apart from a harmless formatting difference."""
    application = ExtractedFields(brand_name="Stone's Throw")
    label = ExtractedFields(brand_name="Stone's Throe")

    check = _check(compare_fields(application, label), "brand_name")

    assert check.status == "review"
    assert 0.88 <= check.confidence < 1.0


def test_brand_clearly_different_names_are_a_mismatch() -> None:
    application = ExtractedFields(brand_name="Stone's Throw")
    label = ExtractedFields(brand_name="Old Tom Distillery")

    check = _check(compare_fields(application, label), "brand_name")

    assert check.status == "mismatch"


def test_brand_case_and_whitespace_variation_still_auto_matches() -> None:
    """Requirement 2.4 example: STONE'S THROW vs Stone's Throw must match automatically."""
    application = ExtractedFields(brand_name="STONE'S THROW")
    label = ExtractedFields(brand_name="Stone's Throw")

    check = _check(compare_fields(application, label), "brand_name")

    assert check.status == "match"
    assert check.confidence == 1.0


def test_abv_matches_within_half_point_tolerance() -> None:
    application = ExtractedFields(alcohol_content="45%")
    label = ExtractedFields(alcohol_content="45.4% Alc./Vol.")

    check = _check(compare_fields(application, label), "alcohol_content")

    assert check.status == "match"


def test_abv_mismatch_just_outside_tolerance() -> None:
    application = ExtractedFields(alcohol_content="40% Alc./Vol.")
    label = ExtractedFields(alcohol_content="41% Alc./Vol.")

    check = _check(compare_fields(application, label), "alcohol_content")

    assert check.status == "mismatch"


def test_abv_compares_by_value_ignoring_proof_annotation() -> None:
    """Requirement 2.4: ABV should be compared by value rather than superficial formatting -
    the application's "(90 Proof)" annotation must not prevent a numeric match."""
    application = ExtractedFields(alcohol_content="45% Alc./Vol. (90 Proof)")
    label = ExtractedFields(alcohol_content="45% ALC/VOL")

    check = _check(compare_fields(application, label), "alcohol_content")

    assert check.status == "match"


def test_abv_unparseable_application_value_is_not_silently_passed() -> None:
    """If the application-side ABV can't be parsed as a number at all, the field must not be
    reported as a match just because the label side looks fine."""
    application = ExtractedFields(alcohol_content="see back label")
    label = ExtractedFields(alcohol_content="45% ALC/VOL")

    check = _check(compare_fields(application, label), "alcohol_content")

    assert check.status == "mismatch"


def test_net_contents_matches_with_identical_formatting() -> None:
    application = ExtractedFields(net_contents="750 mL")
    label = ExtractedFields(net_contents="750 mL")

    check = _check(compare_fields(application, label), "net_contents")

    assert check.status == "match"


def test_net_contents_case_insensitive_match() -> None:
    application = ExtractedFields(net_contents="750 ML")
    label = ExtractedFields(net_contents="750 ml")

    check = _check(compare_fields(application, label), "net_contents")

    assert check.status == "match"


def test_net_contents_spacing_variation_between_number_and_unit_is_normalized() -> None:
    """Requirement 2.4: net contents formatting should be normalized. "750mL" (as an agent
    might type it) and "750 mL" (as OCR reads it with a space) represent the same value and
    must not be reported as a mismatch."""
    application = ExtractedFields(net_contents="750mL")
    label = ExtractedFields(net_contents="750 mL")

    check = _check(compare_fields(application, label), "net_contents")

    assert check.status == "match"


def test_net_contents_normalizes_across_equivalent_units() -> None:
    application = ExtractedFields(net_contents="0.75 L")
    label = ExtractedFields(net_contents="750 mL")

    check = _check(compare_fields(application, label), "net_contents")

    assert check.status == "match"


def test_net_contents_genuinely_different_volumes_still_mismatch() -> None:
    application = ExtractedFields(net_contents="750 mL")
    label = ExtractedFields(net_contents="700 mL")

    check = _check(compare_fields(application, label), "net_contents")

    assert check.status == "mismatch"


def test_class_type_difference_is_surfaced_as_mismatch() -> None:
    application = ExtractedFields(class_type="Kentucky Straight Bourbon Whiskey")
    label = ExtractedFields(class_type="Blended Whiskey")

    check = _check(compare_fields(application, label), "class_type")

    assert check.status == "mismatch"


def test_producer_address_difference_is_surfaced_as_mismatch() -> None:
    application = ExtractedFields(producer="Example Distillery, Kentucky")
    label = ExtractedFields(producer="A Different Distillery, Ohio")

    check = _check(compare_fields(application, label), "producer")

    assert check.status == "mismatch"


def test_producer_statement_prefix_does_not_cause_a_false_mismatch() -> None:
    """Regression test: Claude vision transcribes the producer field verbatim, boilerplate
    statement phrase included (e.g. "DISTILLED AND BOTTLED BY: ..."), while an application
    value an agent types is normally just the name and address with no such phrase - this
    must still match rather than being flagged as a discrepancy purely over the label."""
    application = ExtractedFields(producer="ABC Distillery, Frederick, MD")
    label = ExtractedFields(producer="DISTILLED AND BOTTLED BY: ABC DISTILLERY, FREDERICK, MD")

    check = _check(compare_fields(application, label), "producer")

    assert check.status == "match"
    # The original, unstripped label value is preserved as evidence, not silently rewritten.
    assert check.label_value == "DISTILLED AND BOTTLED BY: ABC DISTILLERY, FREDERICK, MD"


def test_producer_statement_prefix_on_only_one_side_still_matches() -> None:
    """The prefix can appear on either side (application text pasted from the same statement,
    or the label side) - stripping must not be asymmetric."""
    application = ExtractedFields(producer="Bottled by: Example Distillery, Kentucky")
    label = ExtractedFields(producer="Example Distillery, Kentucky")

    check = _check(compare_fields(application, label), "producer")

    assert check.status == "match"


def test_producer_prefix_stripped_but_still_a_near_miss_routes_to_review() -> None:
    """Real Claude vision output (see README.md "Approach" testing notes): the statement
    prefix strips cleanly, but the remaining transcription still differs from a typed
    application value by punctuation alone (no comma between the distillery name and its
    city). That's not an exact match, but it's too close to silently call a mismatch either -
    same near-miss treatment brand_name already gets."""
    application = ExtractedFields(producer="ABC Distillery, Frederick, MD")
    label = ExtractedFields(producer="DISTILLED AND BOTTLED BY: ABC DISTILLERY FREDERICK, MD")

    check = _check(compare_fields(application, label), "producer")

    assert check.status == "review"


def test_warning_missing_entirely_routes_to_review_with_actionable_reason() -> None:
    application = ExtractedFields()
    label = ExtractedFields(government_warning=None)

    check = _check(compare_fields(application, label), "government_warning")

    assert check.status == "review"
    assert check.label_value is None


def test_warning_abbreviated_text_fails_exact_match() -> None:
    """Requirement 2.3: missing, altered, or abbreviated warnings must be flagged."""
    application = ExtractedFields()
    label = ExtractedFields(
        government_warning="GOVERNMENT WARNING: Consumption of alcoholic beverages impairs your ability to drive."
    )

    check = _check(compare_fields(application, label), "government_warning")

    assert check.status == "review"
    assert "exactly" in check.reason
