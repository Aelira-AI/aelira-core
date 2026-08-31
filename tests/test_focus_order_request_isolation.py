"""Request-serving isolation contract for FocusOrder browser analysis."""

from fastapi.testclient import TestClient

from src.api.main import app


def _mounted_api_paths() -> set[str]:
    """Use the resolved schema so nested/lazy included routers are expanded."""
    return set(app.openapi()["paths"])


def test_focus_order_browser_analysis_is_not_mounted_in_api_process() -> None:
    """Browser-heavy FocusOrder analysis has no request-serving execution path."""
    paths = _mounted_api_paths()

    assert "/education/focus-order/analyze" not in paths
    assert "/education/focus-order/analyze-html" not in paths


def test_focus_order_browser_analysis_request_paths_return_not_found() -> None:
    """Resolve the ASGI route table, not just its generated schema."""
    client = TestClient(app)

    url_response = client.post(
        "/education/focus-order/analyze",
        json={"url": "https://example.com", "max_tabs": 10},
    )
    html_response = client.post(
        "/education/focus-order/analyze-html",
        json={
            "html_content": "<button>Example</button>",
            "base_url": "https://example.com",
            "max_tabs": 10,
        },
    )

    assert url_response.status_code == 404
    assert html_response.status_code == 404


def test_non_browser_accessibility_analysis_remains_mounted() -> None:
    """Removing FocusOrder routes must not unmount the sibling CVD API."""
    assert "/education/cvd/analyze" in _mounted_api_paths()
