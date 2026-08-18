"""A fix that satisfies the checker and misleads the reader is not a fix.

Three alt texts reached a real course page during a live walk: an empty
string on a chart, the placeholder "Image content not specified." on a
rubric, and a fluent description of a photograph for an image the model
could not fetch. All three pass axe-core, which only checks that the
attribute exists, so the rescan counted them as fixed and was right to.

An unfixed image is visible to an audit. A falsely fixed one is not.
"""

import pytest

from src.education.remediation.base import IssueCategory, RemediationIssue
from src.education.remediation.html_remediator import HtmlRemediator


def _remediator(html, tmp_path):
    path = tmp_path / "page.html"
    path.write_text(f"<!DOCTYPE html><html><body>{html}</body></html>")
    return HtmlRemediator(str(path), [])


def _alt_issue(location):
    return RemediationIssue(
        id="1",
        category=IssueCategory.ALT_TEXT,
        description="Images must have alternate text",
        severity="critical",
        location=location,
    )


@pytest.mark.parametrize(
    "proposed",
    ["", "   ", "image", "Photo", "Image content not specified.", "n/a", "TODO"],
)
def test_empty_and_placeholder_alt_text_is_refused(proposed):
    assert HtmlRemediator.is_usable_alt_text(proposed) is False


def test_a_real_description_is_accepted():
    assert (
        HtmlRemediator.is_usable_alt_text(
            "Bar chart of weekly readings, rising from two to nine"
        )
        is True
    )


def test_an_unreachable_image_is_left_for_a_human(tmp_path):
    """Content stored by an LMS refers to images by relative path, which
    nothing here can resolve, so no model saw the image. A description
    produced without it is invention."""
    r = _remediator('<img src="/images/calendar.png">', tmp_path)
    r._load_document()

    applied = r._apply_alt_text_fix(
        _alt_issue("/images/calendar.png"),
        "A person typing on a laptop keyboard, with a blurred background.",
    )

    assert applied is False
    assert "not reachable" in " ".join(r._modifications)
    assert "alt=" not in str(r._soup.find("img"))


def test_a_reachable_image_still_gets_its_description(tmp_path):
    r = _remediator('<img src="https://example.edu/chart.png">', tmp_path)
    r._load_document()

    applied = r._apply_alt_text_fix(
        _alt_issue("https://example.edu/chart.png"),
        "Bar chart of weekly readings, rising from two to nine",
    )

    assert applied is True
    assert r._soup.find("img")["alt"].startswith("Bar chart")


def test_a_placeholder_is_refused_even_for_a_reachable_image(tmp_path):
    r = _remediator('<img src="https://example.edu/chart.png">', tmp_path)
    r._load_document()

    applied = r._apply_alt_text_fix(
        _alt_issue("https://example.edu/chart.png"), "image"
    )

    assert applied is False
    assert r._soup.find("img").get("alt") is None


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = "overloaded" if status_code != 200 else ""

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_a_transient_refusal_is_retried_not_treated_as_an_answer(
    tmp_path, monkeypatch
):
    """The vision endpoint returns 503 under load often enough to matter: one
    call in two failed locally while the next succeeded twenty seconds later.
    Treating that as a final answer leaves an image with no description and
    makes it look undescribable rather than unretried."""
    from unittest.mock import AsyncMock, MagicMock

    from src.education import image_alt_text as module

    image = tmp_path / "chart.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    ok = _Response(
        200,
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "A bar chart of weekly readings"}]},
                }
            ]
        },
    )
    post = AsyncMock(side_effect=[_Response(503), ok])
    client = MagicMock()
    client.post = post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    generator = module.ImageAltTextGenerator()
    generator.gemini_api_key = "test-key"
    text, _elapsed = await generator._generate_with_gemini(str(image), "describe")

    assert text == "A bar chart of weekly readings"
    assert post.await_count == 2
