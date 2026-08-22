"""Isolated, deterministic axe-core execution for LMS-authored HTML."""

from typing import Any, Dict

from bs4 import BeautifulSoup


class DeterministicScanUnavailable(RuntimeError):
    """Raised when a deterministic accessibility scan cannot be trusted."""

    code = "DETERMINISTIC_SCAN_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__("Deterministic accessibility scan unavailable")


def _load_runtime():
    """Load optional browser dependencies at execution time."""
    from axe_playwright_python.async_playwright import Axe
    from playwright.async_api import async_playwright

    return async_playwright, Axe


def sanitize_scan_html(html: str) -> str:
    """Remove active authored content while preserving static semantics."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "iframe", "object", "embed"]):
        tag.decompose()
    for meta in soup.find_all("meta"):
        if str(meta.get("http-equiv", "")).strip().lower() == "refresh":
            meta.decompose()
    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            lowered = attribute.lower()
            if lowered.startswith("on") or lowered == "srcdoc":
                del tag.attrs[attribute]
    return str(soup)


async def _abort_request(route: Any) -> None:
    await route.abort()


def _validated_response(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise DeterministicScanUnavailable()
    passes = value.get("passes")
    violations = value.get("violations")
    if not isinstance(passes, list) or not isinstance(violations, list):
        raise DeterministicScanUnavailable()
    if not passes and not violations:
        raise DeterministicScanUnavailable()
    for entry in [*passes, *violations]:
        if not isinstance(entry, dict):
            raise DeterministicScanUnavailable()
        rule_id = entry.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise DeterministicScanUnavailable()
        if not isinstance(entry.get("nodes"), list):
            raise DeterministicScanUnavailable()
    return value


async def run_deterministic_axe(html: str) -> Dict[str, Any]:
    """Run bundled axe with authored execution and all networking disabled."""
    browser = None
    try:
        async_playwright, axe_class = _load_runtime()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route("**/*", _abort_request)
            await page.set_content(sanitize_scan_html(html))
            axe_results = await axe_class().run(page, options=None)
            return _validated_response(getattr(axe_results, "response", None))
    except DeterministicScanUnavailable:
        raise
    except Exception as exc:
        raise DeterministicScanUnavailable() from exc
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
