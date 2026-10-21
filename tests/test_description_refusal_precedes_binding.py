"""A description is refused for what it says before its image is bound.

Two refusals protect a generated image description. A description that holds
mathematical markup, raw or ambiguous LaTeX, is refused whole before it is
truncated or placed. A description of a draw that cannot be bound to one image
alone, because another image fits the same rectangle, is refused with that
reason. Both must hold together: the markup is judged first and the draw is
never looked at for such a description, and a plain description of an unbindable
draw is refused for the draw, with no Figure and no HTML sidecar.

The tests run the real scanner, remediator, tagger and saved-file rescan on an
untagged page. The provider describes each image by the pixel width that
identifies it and a spy records which images the remediator tried to bind.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from src.education.math_speech import IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON
from src.education.remediation.pdf_remediator import PdfRemediator
from tests.test_ambiguous_figure_binding import (
    FIRST,
    SECOND,
    _carried,
    _figures,
    _fixed_alt_ids,
    _no_sidecar,
    _reasons,
    _write_draws,
)
from tests.test_generated_figure_binding import _phantom_figures, _remediate

pytestmark = pytest.mark.unit

MARKUP = IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON
AMBIGUOUS_DRAW = "image_draw_ambiguous"
FAR = (350.0, 300.0, 450.0, 400.0)  # clear of FIRST and SECOND
PLAIN = "A bar chart of the results"

# Raw LaTeX, LaTeX that the spoken-word table cannot render without ambiguity,
# delimited LaTeX, and a bare command.
LATEX_DESCRIPTIONS = [
    pytest.param(r"\frac{1}{2} of the circle is shaded", id="raw-latex"),
    pytest.param(r"\frac12 of the circle is shaded", id="ambiguous-latex"),
    pytest.param("The area is $x^{2} + 1$ square units", id="dollar-span"),
    pytest.param(r"The side is \sqrt x units long", id="bare-command"),
]


class _ContentProvider:
    """Answers each image by the width that identifies it, or cannot."""

    purpose = "alt_text"
    provider = "test-provider"
    model = "test-model"

    def __init__(self, content: dict[int, str]) -> None:
        self.content = content
        self.calls: list[int] = []

    def analyze_image_sync(self, *, image_data: bytes, prompt: str, **_: Any):
        with Image.open(io.BytesIO(image_data)) as image:
            index = (image.width - 60) // 10
        self.calls.append(index)
        text = self.content.get(index)
        if text:
            return {"success": True, "content": text}
        return {"success": False, "content": ""}


@pytest.fixture
def looked_at(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The images whose draw the remediator tried to bind a Figure to."""
    seen: list[int] = []
    real = PdfRemediator._image_layout_bbox

    def spy(self: PdfRemediator, issue: Any):
        seen.append(issue.metadata["image_index"])
        return real(self, issue)

    monkeypatch.setattr(PdfRemediator, "_image_layout_bbox", spy)
    return seen


@pytest.mark.parametrize("description", LATEX_DESCRIPTIONS)
def test_markup_in_a_description_is_refused_before_the_draw_is_looked_at(
    tmp_path: Path, looked_at: list[int], description: str
) -> None:
    """The draws overlap, so binding would refuse them, but markup is judged first."""
    source = tmp_path / "source.pdf"
    _write_draws(source, [FIRST, SECOND])
    provider = _ContentProvider({0: description, 1: description})

    remediator, result = _remediate(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert sorted(provider.calls) == [0, 1]
        assert _reasons(result, remediator) == {0: MARKUP, 1: MARKUP}
        # No image was bound or even located, so no draw reason can have decided.
        assert looked_at == []
        assert not _fixed_alt_ids(result)
        assert _carried(result.output_file) == [(False, None), (False, None)]
        assert [alt for alt, _ in _figures(result.output_file)] == [None, None]
        assert _phantom_figures(result.output_file) == []
        _no_sidecar(remediator, result)
        assert result.verification_passed, result.verification_result
    finally:
        result.close_output_claim()


def test_a_plain_description_of_an_unbindable_draw_is_refused_for_the_draw(
    tmp_path: Path, looked_at: list[int]
) -> None:
    """The same layout with clean descriptions reaches the binding and is refused."""
    source = tmp_path / "source.pdf"
    _write_draws(source, [FIRST, SECOND])
    provider = _ContentProvider({0: PLAIN, 1: PLAIN})

    remediator, result = _remediate(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert _reasons(result, remediator) == {
            0: AMBIGUOUS_DRAW,
            1: AMBIGUOUS_DRAW,
        }
        assert sorted(looked_at) == [0, 1]
        assert not _fixed_alt_ids(result)
        assert _carried(result.output_file) == [(False, None), (False, None)]
        assert [alt for alt, _ in _figures(result.output_file)] == [None, None]
        assert _phantom_figures(result.output_file) == []
        _no_sidecar(remediator, result)
        assert result.verification_passed, result.verification_result
    finally:
        result.close_output_claim()


def test_each_image_is_refused_for_its_own_reason_and_the_bindable_one_is_described(
    tmp_path: Path, looked_at: list[int]
) -> None:
    """Markup, an overlapping draw and a bindable draw on one page, side by side.

    The first image's description holds LaTeX and is refused for that. The second
    is described plainly but overlaps the first and is refused for its draw. The
    third is described plainly and fits no other image, so it is bound to its own
    Figure and credited.
    """
    source = tmp_path / "source.pdf"
    _write_draws(source, [FIRST, SECOND, FAR])
    provider = _ContentProvider({0: r"\frac{1}{2} is shaded", 1: PLAIN, 2: PLAIN})

    remediator, result = _remediate(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert _reasons(result, remediator) == {0: MARKUP, 1: AMBIGUOUS_DRAW}
        # The markup image is never bound; the other two are tried.
        assert sorted(looked_at) == [1, 2]
        assert len(_fixed_alt_ids(result)) == 1
        assert _carried(result.output_file) == [
            (False, None),
            (False, None),
            (True, PLAIN),
        ]
        assert sorted(alt for alt, _ in _figures(result.output_file) if alt) == [PLAIN]
        assert _phantom_figures(result.output_file) == []
        assert result.verification_passed, result.verification_result
        assert result.verification_result.unavailable_checks == []
    finally:
        result.close_output_claim()
