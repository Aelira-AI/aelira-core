"""The OIDC login keeps a loopback launch URL and upgrades every other plain-HTTP one."""

import pytest

from src.api.lti_routes import _tool_launch_uri


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("http://tool.example.edu/lti/launch", "https://tool.example.edu/lti/launch"),
        ("https://tool.example.edu/lti/launch", "https://tool.example.edu/lti/launch"),
        ("http://localhost:18300/lti/launch", "http://localhost:18300/lti/launch"),
        ("http://127.0.0.1:8000/lti/launch", "http://127.0.0.1:8000/lti/launch"),
        ("http://[::1]:8000/lti/launch", "http://[::1]:8000/lti/launch"),
        (
            "http://localhost.example.edu/lti/launch",
            "https://localhost.example.edu/lti/launch",
        ),
        (
            "http://tool.example.edu/lti/launch?next=http://x",
            "https://tool.example.edu/lti/launch?next=http://x",
        ),
    ],
)
def test_launch_uri_scheme(uri, expected):
    assert _tool_launch_uri(uri) == expected
