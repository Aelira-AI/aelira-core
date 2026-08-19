import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.api import brightspace_lti_routes, lti_routes
from src.integrations.brightspace_lti import BrightspaceLaunchData
from src.integrations.canvas_lti import CanvasLaunchData

DENIED_ROLE = "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"


def _request():
    request = MagicMock()
    request.form = AsyncMock(return_value={"id_token": "validated-elsewhere"})
    request.cookies = {}
    request.headers = {}
    return request


def _canvas_launch_data():
    return CanvasLaunchData(
        user_id="learner-1",
        user_name="Learner",
        user_email=None,
        course_id="course-1",
        course_name="Course",
        roles=[DENIED_ROLE],
        is_instructor=False,
        is_student=True,
        resource_link_id="link-1",
        deployment_id="deployment-1",
        platform_id="https://canvas.example.edu",
        client_id="client-1",
        nonce="nonce-1",
        placement="course_navigation",
        custom_params={},
    )


def _brightspace_launch_data():
    return BrightspaceLaunchData(
        user_id="learner-1",
        user_name="Learner",
        user_email=None,
        course_id="course-1",
        course_name="Course",
        roles=[DENIED_ROLE],
        is_instructor=False,
        is_student=True,
        resource_link_id="link-1",
        deployment_id="deployment-1",
        platform_id="https://brightspace.example.edu",
        client_id="client-1",
        nonce="nonce-1",
        custom_params={},
    )


def _service(launch_data):
    service = MagicMock()
    service.is_configured.return_value = True
    service.validate_launch.return_value = MagicMock()
    service.extract_launch_data.return_value = launch_data
    return service


def _assert_neutral_denial(response):
    assert response.status_code == 403
    assert response.body == b"LTI launch not authorized."


def test_canvas_launch_denies_before_database_or_launch_side_effects():
    service = _service(_canvas_launch_data())
    db = MagicMock()

    response = asyncio.run(lti_routes.lti_launch(_request(), service, db))

    _assert_neutral_denial(response)
    assert db.mock_calls == []
    service.get_issuer_from_launch.assert_not_called()
    service.is_deep_link_launch.assert_not_called()


def test_canvas_development_launch_without_state_still_enforces_staff_gate(
    monkeypatch,
):
    monkeypatch.setenv("ENV", "development")
    service = _service(_canvas_launch_data())
    db = MagicMock()

    response = asyncio.run(lti_routes.lti_launch(_request(), service, db))

    _assert_neutral_denial(response)
    service.validate_launch.assert_called_once()
    assert db.mock_calls == []


def test_brightspace_launch_denies_before_database_or_launch_side_effects():
    service = _service(_brightspace_launch_data())
    db = MagicMock()

    response = asyncio.run(
        brightspace_lti_routes.brightspace_lti_launch(_request(), service, db)
    )

    _assert_neutral_denial(response)
    assert db.mock_calls == []
    service.get_issuer_from_launch.assert_not_called()
    service.is_deep_link_launch.assert_not_called()


def test_canvas_deep_link_denies_before_deep_link_handling(monkeypatch):
    service = _service(_canvas_launch_data())
    handler = AsyncMock()
    monkeypatch.setattr(lti_routes, "handle_deep_link_launch", handler)

    response = asyncio.run(lti_routes.lti_deep_link(_request(), service))

    _assert_neutral_denial(response)
    handler.assert_not_awaited()


def test_brightspace_deep_link_denies_before_deep_link_handling(monkeypatch):
    service = _service(_brightspace_launch_data())
    handler = AsyncMock()
    monkeypatch.setattr(
        brightspace_lti_routes, "handle_brightspace_deep_link_launch", handler
    )

    response = asyncio.run(
        brightspace_lti_routes.brightspace_lti_deep_link(_request(), service)
    )

    _assert_neutral_denial(response)
    handler.assert_not_awaited()
