"""Canonical regulatory deadline resolution contracts."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from src.education.deadline_config import DeadlineService, ISO_ALPHA_2_COUNTRY_CODES


def _department(**overrides):
    values = {
        "country_code": None,
        "regulatory_framework": None,
        "title_ii_entity_class": None,
        "custom_deadline": None,
        "custom_deadline_verified_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_regulatory_profile_country_allowlist_is_complete_iso_alpha_2():
    assert len(ISO_ALPHA_2_COUNTRY_CODES) == 249
    assert {"AU", "CA", "DE", "GB", "US"} < ISO_ALPHA_2_COUNTRY_CODES
    assert "ZZ" not in ISO_ALPHA_2_COUNTRY_CODES


@pytest.mark.parametrize(
    ("entity_class", "expected"),
    [
        ("large", date(2027, 4, 26)),
        ("small_or_special_district", date(2028, 4, 26)),
    ],
)
def test_us_title_ii_uses_explicit_entity_class(entity_class, expected):
    info = DeadlineService.for_department(
        _department(
            country_code="US",
            regulatory_framework="US_ADA_TITLE_II",
            title_ii_entity_class=entity_class,
        ),
        as_of=date(2026, 8, 28),
    )

    assert info.applicability == "dated_deadline"
    assert info.deadline_date == expected
    assert info.framework_code == "US_ADA_TITLE_II"
    assert info.standard == "WCAG 2.1 Level AA"


def test_us_title_ii_without_entity_class_fails_closed():
    info = DeadlineService.for_department(
        _department(country_code="US", regulatory_framework="US_ADA_TITLE_II"),
        as_of=date(2026, 8, 28),
    )

    assert info.applicability == "configuration_required"
    assert info.has_deadline is False
    assert info.deadline_date is None
    assert info.days_remaining is None
    assert info.framework_code == "US_ADA_TITLE_II"


def test_absent_profile_does_not_default_to_a_us_deadline():
    info = DeadlineService.for_department(_department(), as_of=date(2026, 8, 28))

    assert info.applicability == "configuration_required"
    assert info.framework_code == "UNCONFIGURED"
    assert info.deadline_date is None


@pytest.mark.parametrize(
    ("country", "framework", "deadline"),
    [
        ("DE", "EU_EAA", date(2025, 6, 28)),
        ("GB", "UK_PSBAR", date(2020, 9, 23)),
        ("CA", "CA_AODA", date(2025, 1, 1)),
    ],
)
def test_country_profiles_resolve_implemented_dated_frameworks(
    country, framework, deadline
):
    info = DeadlineService.for_department(
        _department(country_code=country, regulatory_framework=framework),
        as_of=date(2026, 8, 28),
    )

    assert info.applicability == "dated_deadline"
    assert info.framework_code == framework
    assert info.deadline_date == deadline
    assert info.is_past_deadline is True
    assert info.days_remaining == 0


def test_australia_is_an_ongoing_obligation_without_a_date():
    info = DeadlineService.for_department(
        _department(country_code="AU"), as_of=date(2026, 8, 28)
    )

    assert info.applicability == "ongoing_no_date"
    assert info.has_deadline is False
    assert info.deadline_date is None
    assert info.deadline_label is None
    assert info.days_remaining is None
    assert info.framework_code == "AU_DDA"


def test_explicit_none_is_not_applicable():
    info = DeadlineService.for_department(
        _department(country_code="US", regulatory_framework="NONE"),
        as_of=date(2026, 8, 28),
    )

    assert info.applicability == "not_applicable"
    assert info.has_deadline is False
    assert info.framework_code == "NONE"


def test_custom_deadline_keeps_the_underlying_framework_and_standard():
    info = DeadlineService.for_department(
        _department(
            country_code="DE",
            regulatory_framework="EU_EAA",
            custom_deadline=datetime(2027, 2, 3, 12, tzinfo=timezone.utc),
            custom_deadline_verified_at=datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
        ),
        as_of=date(2027, 2, 1),
    )

    assert info.applicability == "dated_deadline"
    assert info.deadline_date == date(2027, 2, 3)
    assert info.deadline_label == "February 3, 2027"
    assert info.days_remaining == 2
    assert info.framework_code == "EU_EAA"
    assert info.framework_name == "European Accessibility Act (EAA)"
    assert info.standard == "EN 301 549 (aligned with WCAG 2.1 AA)"


def test_unverified_historical_custom_deadline_cannot_override_canonical_date():
    info = DeadlineService.for_department(
        _department(
            country_code="DE",
            regulatory_framework="EU_EAA",
            custom_deadline=datetime(2030, 1, 15, tzinfo=timezone.utc),
            custom_deadline_verified_at=None,
        ),
        as_of=date(2026, 8, 28),
    )

    assert info.deadline_date == date(2025, 6, 28)
    assert (
        info.message
        != "An organization-specific accessibility target date is configured."
    )


def test_direct_trusted_deadline_resolution_preserves_custom_override_contract():
    info = DeadlineService.get_deadline_info(
        country_code="DE",
        regulatory_framework="EU_EAA",
        custom_deadline=datetime(2030, 1, 15, tzinfo=timezone.utc),
        as_of=date(2029, 1, 1),
    )

    assert info.deadline_date == date(2030, 1, 15)
    assert (
        info.message
        == "An organization-specific accessibility target date is configured."
    )


def test_explicit_implemented_framework_overrides_country():
    info = DeadlineService.for_department(
        _department(country_code="AU", regulatory_framework="EU_EAA"),
        as_of=date(2024, 1, 1),
    )

    assert info.framework_code == "EU_EAA"
    assert info.deadline_date == date(2025, 6, 28)


@pytest.mark.parametrize("country", ["CA", "DE", "GB"])
def test_country_alone_does_not_infer_narrower_dated_law(country):
    info = DeadlineService.for_department(
        _department(country_code=country), as_of=date(2026, 8, 28)
    )

    assert info.applicability == "configuration_required"
    assert info.has_deadline is False
    assert info.deadline_date is None


@pytest.mark.parametrize(
    "department",
    [
        _department(country_code="US", regulatory_framework="NOT_REAL"),
        _department(country_code="US", regulatory_framework="US_SECTION_508"),
        _department(country_code="DE", regulatory_framework="EU_WAD"),
        _department(country_code="EU"),
        _department(country_code="DE", title_ii_entity_class="large"),
        _department(
            country_code="US",
            regulatory_framework="US_ADA_TITLE_II",
            title_ii_entity_class="medium",
        ),
    ],
)
def test_invalid_unimplemented_or_conflicting_profiles_fail_closed(department):
    info = DeadlineService.for_department(department, as_of=date(2026, 8, 28))

    assert info.applicability == "configuration_required"
    assert info.has_deadline is False
    assert info.deadline_date is None


def test_deadline_day_and_past_state_are_deterministic():
    department = _department(
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="large",
    )

    on_day = DeadlineService.for_department(department, as_of=date(2027, 4, 26))
    past = DeadlineService.for_department(department, as_of=date(2027, 4, 27))

    assert on_day.days_remaining == 0
    assert on_day.is_past_deadline is False
    assert on_day.urgency == "critical"
    assert past.days_remaining == 0
    assert past.is_past_deadline is True
    assert past.urgency == "critical"


def test_report_on_track_is_null_without_date_and_evaluated_on_deadline_day():
    undated = DeadlineService.get_deadline_for_report(
        country_code="AU",
        issues_total=0,
        as_of=date(2026, 8, 28),
    )
    deadline_day = DeadlineService.get_deadline_for_report(
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="large",
        issues_total=1,
        as_of=date(2027, 4, 26),
    )

    assert undated["on_track"] is None
    assert deadline_day["days_remaining"] == 0
    assert deadline_day["on_track"] is False

    past = DeadlineService.get_deadline_for_report(
        country_code="DE",
        regulatory_framework="EU_EAA",
        issues_total=0,
        as_of=date(2026, 8, 28),
    )
    assert past["is_past_deadline"] is True
    assert past["on_track"] is None


def test_serializer_is_json_safe_complete_and_message_is_bounded():
    info = DeadlineService.for_department(
        _department(country_code="AU"), as_of=date(2026, 8, 28)
    )

    payload = info.to_dict()

    assert payload["deadline_date"] is None
    assert payload["deadline_label"] is None
    assert payload["applicability"] == "ongoing_no_date"
    assert payload["description"] == payload["message"]
    assert len(payload["message"]) <= 160
