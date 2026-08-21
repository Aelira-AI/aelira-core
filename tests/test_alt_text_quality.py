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
    r._image_was_reachable = lambda _src: True

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


"""Strict shared validation for model-generated image descriptions."""

from unittest.mock import MagicMock

from bs4 import BeautifulSoup
from PIL import Image

from src.education.image_alt_text import ImageAltTextGenerator


def _image(tmp_path, suffix=".png", format_name="PNG"):
    path = tmp_path / f"fixture{suffix}"
    Image.new("RGB", (8, 6), "blue").save(path, format=format_name)
    return path


@pytest.mark.asyncio
async def test_generator_and_html_remediator_share_normalized_alt_text(tmp_path):
    client = MagicMock(provider="gemini")
    client.analyze_image_sync.return_value = {
        "success": True,
        "content": "Alt Text:  Blå square 🟦  ",
        "provider": "gemini",
    }
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.generate_alt_text(str(_image(tmp_path)))

    assert result["success"] is True
    assert result["alt_text"] == "Blå square 🟦"
    assert (
        HtmlRemediator.normalize_usable_alt_text("Alt Text:  Blå square 🟦  ")
        == "Blå square 🟦"
    )


@pytest.mark.parametrize(
    "model_text",
    [
        "Blue\x00square",
        "Blue\tsquare",
        "Blue\nsquare",
        "Blue\x1bsquare",
        "Blue\x85square",
        "Blue\u202esquare",
        "Blue\u200bsquare",
        "Blue\ud800square",
    ],
)
def test_control_bearing_alt_text_is_rejected_without_repair(model_text):
    assert HtmlRemediator.normalize_usable_alt_text(model_text) is None


def test_ordinary_spaces_non_ascii_letters_and_emoji_are_accepted():
    assert (
        HtmlRemediator.normalize_usable_alt_text("Blå square beside café 🟦")
        == "Blå square beside café 🟦"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_text",
    [
        "UNKNOWN",
        "Error: provider failed",
        "Unable to describe image",
        "Cannot determine contents",
        "image",
        "A blue square...",
        "A blue square…",
        "x" * 501,
        "Blue\x00square",
        "Blue\tsquare",
        "Blue\nsquare",
        "Blue\x1bsquare",
        "Blue\x85square",
        "Blue\u202esquare",
        "Blue\u200bsquare",
        "Blue\ud800square",
    ],
)
async def test_bad_or_apparently_truncated_alt_text_never_reaches_html(
    tmp_path, model_text
):
    client = MagicMock(provider="gemini")
    client.analyze_image_sync.return_value = {
        "success": True,
        "content": model_text,
        "provider": "gemini",
    }
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.generate_alt_text(str(_image(tmp_path)))

    assert result["success"] is False
    assert HtmlRemediator.is_usable_alt_text(model_text) is False


def test_beautifulsoup_alt_insertion_escapes_attribute_content(tmp_path):
    source = tmp_path / "page.html"
    source.write_text('<html><body><img src="chart.png"></body></html>')
    remediator = HtmlRemediator(str(source), [])
    remediator._load_document()
    issue = RemediationIssue(
        issue_id="image-alt",
        category=IssueCategory.ALT_TEXT,
        severity="high",
        description="missing alt",
        location="chart.png",
    )
    remediator._image_was_reachable = lambda _src: True

    assert remediator._apply_alt_text_fix(issue, 'Chart "A" < 5 & rising') is True
    rendered = str(remediator._soup)
    parsed = BeautifulSoup(rendered, "html.parser")
    assert parsed.img["alt"] == 'Chart "A" < 5 & rising'
    assert "&lt; 5 &amp; rising" in rendered
    assert "< 5 & rising" not in rendered


def test_html_remediator_leaves_control_bearing_alt_text_for_manual_fix(tmp_path):
    source = tmp_path / "page.html"
    source.write_text('<html><body><img src="chart.png"></body></html>')
    remediator = HtmlRemediator(str(source), [])
    remediator._load_document()
    remediator._image_was_reachable = lambda _src: True

    applied = remediator._apply_alt_text_fix(_alt_issue("chart.png"), "Blue\tsquare")

    assert applied is False
    assert remediator._soup.find("img").get("alt") is None
    assert "Left alt text to a human" in " ".join(remediator._modifications)
