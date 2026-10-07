"""HTTPX must not log caller image URLs under application INFO logging."""

import logging

import httpx
import pytest


# These tests use only MockTransport; they do not need the suite database.
@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield


SYNTHETIC_URL = "https://synthetic.example/private-course?token=synthetic-canary"


async def _mock_image_request():
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"ok"))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get(SYNTHETIC_URL)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_httpx_info_positive_control_contains_full_synthetic_url(caplog):
    import src.api.main  # noqa: F401 - initialize the application logging policy

    with caplog.at_level(logging.INFO, logger="httpx"):
        await _mock_image_request()

    assert SYNTHETIC_URL in caplog.text


@pytest.mark.asyncio
async def test_app_policy_omits_httpx_url_and_keeps_bounded_refusal(caplog):
    from src.api import main

    assert logging.getLogger("httpx").level == logging.WARNING
    with caplog.at_level(logging.INFO):
        await _mock_image_request()
        result = await main.generate_image_alt_text(
            '<img src="http://127.0.0.1/private-course?token=synthetic-canary">'
        )

    assert result is None
    assert "synthetic-canary" not in caplog.text
    assert "/private-course" not in caplog.text
    assert "Blocked image URL: (ValueError)" in caplog.text
