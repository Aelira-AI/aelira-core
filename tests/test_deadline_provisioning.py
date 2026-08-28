"""Department provisioning carries the regulatory deadline profile."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.api.auth_routes import (
    CreateDepartmentRequest,
    _department_response,
    _new_department_from_request,
)


def _request(**overrides):
    values = {
        "name": "Accessibility",
        "institution": "Example University",
        "contact_email": "admin@example.edu",
        "contact_name": "Admin",
    }
    values.update(overrides)
    return CreateDepartmentRequest(**values)


def test_legacy_provisioning_payload_remains_valid_without_false_us_defaults():
    request = _request()
    department = _new_department_from_request(request)

    assert department.country_code is None
    assert department.regulatory_framework is None
    assert department.title_ii_entity_class is None
    assert department.custom_deadline is None


def test_provisioning_request_and_response_round_trip_deadline_profile():
    custom = datetime(2027, 6, 1, tzinfo=timezone.utc)
    request = _request(
        country_code="us",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="small_or_special_district",
        custom_deadline=custom,
    )
    department = _new_department_from_request(request)
    department.id = "department-1"
    department.created_at = datetime(2026, 8, 28, tzinfo=timezone.utc)

    response = _department_response(department)

    assert response.country_code == "US"
    assert response.regulatory_framework == "US_ADA_TITLE_II"
    assert response.title_ii_entity_class == "small_or_special_district"
    assert response.custom_deadline == custom


@pytest.mark.parametrize(
    "overrides",
    [
        {"country_code": "USA"},
        {"regulatory_framework": "NOT_REAL"},
        {"title_ii_entity_class": "medium"},
    ],
)
def test_provisioning_rejects_malformed_profile_values(overrides):
    with pytest.raises(ValidationError):
        _request(**overrides)


def test_department_response_is_compatible_with_pre_profile_rows():
    response = _department_response(
        SimpleNamespace(
            id="legacy",
            name="Legacy",
            institution="Example",
            contact_email="legacy@example.edu",
            tier="department",
            max_users=50,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            country_code=None,
            regulatory_framework=None,
            title_ii_entity_class=None,
            custom_deadline=None,
        )
    )

    assert response.country_code is None
    assert response.regulatory_framework is None
    assert response.title_ii_entity_class is None
    assert response.custom_deadline is None
