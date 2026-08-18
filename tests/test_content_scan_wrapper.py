"""The scanning wrapper must not put words in the author's mouth.

A body fragment has to be wrapped in a document before axe-core will run.
The wrapper used to be a bare skeleton, which is not neutral: with no
landmark and no first-level heading, axe reported region,
landmark-one-main and page-has-heading-one against content whose author
never had the chance to supply them. On a real course page that was three
of five findings and 12.5 points of score, none of it fixable by the person
being told to fix it.

The wrapper now mirrors what the LMS renders. The other half of that
bargain is that nothing it adds may ever reach the stored content, which is
what these tests hold in place.
"""

from src.education.canvas_content_scanner import (
    _unwrap_html_fragment,
    _wrap_html_fragment,
)

FRAGMENT = '<h3>Week one</h3><p>Readings</p><img src="chart.png">'


def test_the_wrapper_supplies_the_context_the_lms_renders():
    doc = _wrap_html_fragment(FRAGMENT, "Welcome to Nursing 110")

    assert "<main>" in doc
    assert "<h1>Welcome to Nursing 110</h1>" in doc
    assert "<title>Welcome to Nursing 110</title>" in doc
    assert FRAGMENT in doc


def test_a_title_carrying_markup_cannot_break_out_of_the_heading():
    doc = _wrap_html_fragment("<p>x</p>", '<script>alert("x")</script>')

    assert "<script>" not in doc
    assert "&lt;script&gt;" in doc


def test_unwrapping_returns_the_author_content_and_nothing_we_added():
    doc = _wrap_html_fragment(FRAGMENT, "Welcome to Nursing 110")

    recovered = _unwrap_html_fragment(doc)

    # The parser writes void elements self-closed, which is the only
    # difference it is allowed to make; the author's elements all survive.
    assert "<h3>Week one</h3>" in recovered
    assert "<p>Readings</p>" in recovered
    assert 'src="chart.png"' in recovered

    # The landmark and heading belong to the LMS, not to the item.
    assert "<main>" not in recovered
    assert "<h1>" not in recovered
    assert "Welcome to Nursing 110" not in recovered

    # Round-tripping again changes nothing further, so repeated scans
    # cannot accumulate edits in the stored content.
    assert _unwrap_html_fragment(_wrap_html_fragment(recovered, "t")) == recovered


def test_a_document_without_our_marker_still_unwraps_to_its_body():
    plain = "<!DOCTYPE html><html><body><p>Hello</p></body></html>"

    assert _unwrap_html_fragment(plain) == "<p>Hello</p>"


def test_an_untitled_item_still_produces_a_heading():
    doc = _wrap_html_fragment("<p>x</p>", "")

    assert "<h1>Untitled</h1>" in doc
