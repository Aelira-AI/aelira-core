"""Adversarial saved-export checks for literal authored semantic evidence."""

import pytest
from PIL import Image

from src.education.latex_semantics import (
    extract_semantics,
    save_html_semantics,
    verify_html_semantics,
)

TABLE_SOURCE = (
    r"\tagpdfsetup{table/header-rows={1},table/header-columns={1}}"
    r"\begin{tabular}{cc}A&B\\C&D\end{tabular}"
)
TABLE_HTML = (
    "<table><tr><td>A</td><td>B</td></tr>" "<tr><td>C</td><td>D</td></tr></table>"
)


@pytest.fixture
def table_files(tmp_path):
    source, candidate = tmp_path / "source.tex", tmp_path / "saved.html"
    source.write_text(TABLE_SOURCE, encoding="utf-8")
    candidate.write_text(TABLE_HTML, encoding="utf-8")
    return source, candidate


@pytest.mark.parametrize(
    "html",
    [
        "<div hidden>" + TABLE_HTML + "</div>",
        '<div aria-hidden="true">' + TABLE_HTML + "</div>",
        "<div inert>" + TABLE_HTML + "</div>",
        '<div style="display:none">' + TABLE_HTML + "</div>",
        "<dialog>" + TABLE_HTML + "</dialog>",
        "<style>table {display:/**/none}</style>" + TABLE_HTML,
        "<style>:root {--hidden:none}table {display:var(--hidden)}</style>"
        + TABLE_HTML,
        '<style>td:before {content:"Wrong"}</style>' + TABLE_HTML,
        TABLE_HTML.replace("<table>", '<table role="presentation">'),
        TABLE_HTML.replace("<table>", '<table role="none">'),
        TABLE_HTML.replace("<tr>", '<tr aria-hidden="true">', 1),
        TABLE_HTML.replace("<tr>", '<tr role="presentation">', 1),
        TABLE_HTML.replace("A</td>", '<span aria-hidden="true">A</span></td>'),
        TABLE_HTML.replace("A</td>", '<span aria-label="Wrong">A</span></td>'),
    ],
)
def test_inaccessible_or_overridden_html_is_not_semantic_evidence(table_files, html):
    source, candidate = table_files
    candidate.write_text(html, encoding="utf-8")
    assert not save_html_semantics(source, candidate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda html: html + "<svg><text>Uninspected diagram</text></svg>",
        lambda html: html.replace("<th ", '<th aria-label="Wrong" ', 1),
        lambda html: html.replace("<th ", '<th colspan="2" ', 1),
        lambda html: html.replace("<th ", '<th rowspan="2" ', 1),
        lambda html: html.replace("<th ", '<th role="presentation" ', 1),
        lambda html: "<div hidden>" + html + "</div>",
        lambda html: html.replace("A</th>", '<span aria-hidden="true">A</span></th>'),
    ],
)
def test_saved_verifier_rechecks_structure_and_accessible_content(table_files, mutate):
    source, candidate = table_files
    assert save_html_semantics(source, candidate)
    candidate.write_text(mutate(candidate.read_text()), encoding="utf-8")
    assert not verify_html_semantics(source, candidate)


@pytest.mark.parametrize(
    "source",
    [
        r"\verb|\includegraphics[alt={Cat}]{cat.png}|",
        r"\begin{verbatim}\includegraphics[alt={Cat}]{cat.png}\end{verbatim}",
        r"\gdef\draw{\includegraphics[alt={Cat}]{cat.png}}",
        r"\NewDocumentCommand{\draw}{}{\includegraphics[alt={Cat}]{cat.png}}",
        r"\newenvironment{unused}{\includegraphics[alt={Cat}]{cat.png}}{}",
        r"\begin{comment}\includegraphics[alt={Cat}]{cat.png}\end{comment}",
        r"\savebox{\boxname}{\includegraphics[alt={Cat}]{cat.png}}",
        r"\\includegraphics[alt={Cat}]{cat.png}",
        r"\begin{verbatim}" + TABLE_SOURCE + r"\end{verbatim}",
    ],
)
def test_literal_examples_and_macro_definitions_are_not_authored_relationships(source):
    contract = extract_semantics(source)
    assert contract.issues or not (contract.graphics or contract.tables)


def test_even_backslashes_do_not_escape_a_comment_marker():
    contract = extract_semantics(r"\\% \includegraphics[alt={Cat}]{cat.png}")
    assert not contract.graphics


@pytest.mark.parametrize(
    "source",
    [
        r"\includegraphics[alt={Cat},alt ={Dog}]{cat.png}",
        r"\tagpdfsetup{table/header-rows={1},table/header-rows ={2}}"
        r"\begin{tabular}{cc}A&B\\C&D\end{tabular}",
    ],
)
def test_whitespace_does_not_hide_duplicate_semantic_options(source):
    assert extract_semantics(source).issues


@pytest.mark.parametrize(
    "html",
    [
        '<img src="cat.png" srcset="dog.png 1x">',
        '<picture><source srcset="dog.png"><img src="cat.png"></picture>',
    ],
)
def test_alternate_browser_image_selection_cannot_borrow_asset_alternative(
    tmp_path, html
):
    source, candidate = tmp_path / "source.tex", tmp_path / "saved.html"
    Image.new("RGB", (1, 1), "red").save(tmp_path / "cat.png")
    source.write_text(r"\includegraphics[alt={Cat}]{cat.png}", encoding="utf-8")
    candidate.write_text(html, encoding="utf-8")
    assert not save_html_semantics(source, candidate)


def test_saved_verifier_rechecks_browser_image_selection(tmp_path):
    source, candidate = tmp_path / "source.tex", tmp_path / "saved.html"
    Image.new("RGB", (1, 1), "red").save(tmp_path / "cat.png")
    source.write_text(r"\includegraphics[alt={Cat}]{cat.png}", encoding="utf-8")
    candidate.write_text('<img src="cat.png">', encoding="utf-8")
    assert save_html_semantics(source, candidate)
    candidate.write_text(
        candidate.read_text().replace("<img ", '<img srcset="dog.png 1x" '),
        encoding="utf-8",
    )
    assert not verify_html_semantics(source, candidate)


@pytest.mark.parametrize("role", ["none", "button", "presentation"])
def test_saved_verifier_rechecks_informative_image_role(tmp_path, role):
    source, candidate = tmp_path / "source.tex", tmp_path / "saved.html"
    Image.new("RGB", (1, 1), "red").save(tmp_path / "cat.png")
    source.write_text(r"\includegraphics[alt={Cat}]{cat.png}", encoding="utf-8")
    candidate.write_text('<img src="cat.png">', encoding="utf-8")
    assert save_html_semantics(source, candidate)
    candidate.write_text(
        candidate.read_text().replace("<img ", f'<img role="{role}" '), encoding="utf-8"
    )
    assert not verify_html_semantics(source, candidate)


def test_correct_authored_header_edges_are_verified(table_files):
    source, candidate = table_files
    assert save_html_semantics(source, candidate)
    assert verify_html_semantics(source, candidate)
    # D must reference both the B column header and C row header.
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(candidate.read_text(), "html.parser")
    cells = soup.find_all(["td", "th"])
    assert cells[3]["headers"] == [cells[1]["id"], cells[2]["id"]]
    cells[3]["headers"] = [cells[0]["id"]]
    candidate.write_text(str(soup), encoding="utf-8")
    assert not verify_html_semantics(source, candidate)
