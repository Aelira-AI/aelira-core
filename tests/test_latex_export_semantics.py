"""Source-bound saved relationships, including corruption controls."""

import base64
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from PIL import Image
import pytest

from src.education.latex_diagnostics import conversion_session, sha
from src.education.latex_semantics import (
    extract_semantics,
    save_html_semantics,
    verify_html_semantics,
)
from src.education.remediation.latex_converter import LaTeXConverter

ALT = "A points to B. B points to C."
TABLE = r"""\tagpdfsetup{table/header-rows={1},table/header-columns={1}}
\begin{tabular}{lll}
Trial & A & B \\
Red & 1 & 2 \\
Blue & 3 & 4 \\
\end{tabular}"""
HTML_TABLE = "<table><tr><td>Trial</td><td>A</td><td>B</td></tr><tr><td>Red</td><td>1</td><td>2</td></tr><tr><td>Blue</td><td>3</td><td>4</td></tr></table>"


@pytest.fixture
def document(tmp_path):
    source = tmp_path / "source.tex"
    source.write_text(
        r"\documentclass{article}\begin{document}"
        + rf"\includegraphics[alt={{{ALT}}}]{{diagram.png}}"
        + TABLE
        + r"\end{document}"
    )
    Image.new("RGB", (20, 20), "white").save(tmp_path / "diagram.png")
    candidate = tmp_path / "output.html"
    candidate.write_text(
        '<html><body><img src="diagram.png" alt="wrong">'
        + HTML_TABLE
        + "</body></html>"
    )
    return source, candidate


def test_saved_relationships_preserve_both_edges_and_blue_b_trial(document):
    source, candidate = document
    original = source.read_bytes()
    assert save_html_semantics(source, candidate)
    assert source.read_bytes() == original
    soup = BeautifulSoup(candidate.read_text(), "html.parser")
    assert soup.img["alt"] == ALT
    assert (
        base64.b64decode(soup.img["src"].split(",")[1])
        == (source.parent / "diagram.png").read_bytes()
    )
    four = soup.find("td", string="4")
    headers = [soup.find(id=target) for target in four["headers"]]
    assert [h.get_text() for h in headers] == ["B", "Blue"]
    blue = headers[1]
    assert soup.find(id=blue["headers"][0]).get_text() == "Trial"
    assert verify_html_semantics(source, candidate)


@pytest.mark.parametrize("damage", ["edge", "header", "cell", "asset", "decorative"])
def test_saved_corruption_is_refused(document, damage):
    source, candidate = document
    assert save_html_semantics(source, candidate)
    soup = BeautifulSoup(candidate.read_text(), "html.parser")
    if damage == "edge":
        soup.img["alt"] = "A points to B."
    elif damage == "header":
        soup.find("td", string="4")["headers"] = [soup.find("th", string="A")["id"]]
    elif damage == "cell":
        soup.find("td", string="4").string = "3"
    elif damage == "asset":
        soup.img["src"] = "data:image/png;base64,broken"
    else:
        soup.img["role"] = "presentation"
    candidate.write_text(str(soup))
    assert not verify_html_semantics(source, candidate)


@pytest.mark.parametrize(
    "body",
    [
        r"\includegraphics{diagram.png}",
        r"\includegraphics[alt={}]{diagram.png}",
        TABLE.replace(
            r"\tagpdfsetup{table/header-rows={1},table/header-columns={1}}", r"\hline"
        ),
    ],
)
def test_unconfirmed_semantics_never_derived_from_candidate(document, body):
    source, candidate = document
    source.write_text(body)
    assert "semantics_unconfirmed" in extract_semantics(body).issues
    assert not save_html_semantics(source, candidate)


def test_authored_decorative_status_is_preserved(document):
    source, candidate = document
    source.write_text(r"\includegraphics[artifact]{diagram.png}")
    candidate.write_text('<html><img src="diagram.png" alt="invented caption"></html>')
    assert save_html_semantics(source, candidate)
    soup = BeautifulSoup(candidate.read_text(), "html.parser")
    assert soup.img["alt"] == ""
    assert soup.img["role"] == "presentation"


def test_active_content_cannot_change_verified_relationships(document):
    source, candidate = document
    original = candidate.read_text()
    candidate.write_text(
        original.replace("<img ", "<img onload=\"this.alt='Changed'\" ")
    )
    assert not save_html_semantics(source, candidate)
    candidate.write_text(original)
    assert save_html_semantics(source, candidate)
    candidate.write_text(
        candidate.read_text().replace("<img ", "<img onload=\"this.alt='Changed'\" ")
    )
    assert not verify_html_semantics(source, candidate)


@pytest.mark.parametrize(
    "damage", ["asset-name", "swapped-cells", "span", "external", "unsupported"]
)
def test_no_guessing_to_match_candidate(document, damage):
    source, candidate = document
    text = candidate.read_text()
    if damage == "asset-name":
        text = text.replace("diagram.png", "other.png")
    elif damage == "swapped-cells":
        text = text.replace("<td>3</td><td>4</td>", "<td>4</td><td>3</td>")
    elif damage == "span":
        text = text.replace("<td>Trial", '<td colspan="2">Trial')
    elif damage == "external":
        text = text.replace("diagram.png", "https://example.invalid/diagram.png")
    else:
        text += "<svg></svg>"
    candidate.write_text(text)
    assert not save_html_semantics(source, candidate)


def test_pdf_semantics_cannot_be_credited_from_structure_alone(document):
    source, candidate = document
    converter = LaTeXConverter()
    with conversion_session() as stages:
        assert not converter._preserve_semantics(source, candidate, "pdf")
    assert stages[-1].semantics_profile == "literal-relationships-v1"
    assert {d.code for d in stages[-1].diagnostics} == {"semantics_unsupported"}
    assert stages[-1].input_sha256 == sha(source.read_bytes())
    assert ALT not in stages[-1].model_dump_json()


def test_wrapper_refuses_lost_cells_and_does_not_fallback(document, monkeypatch):
    source, candidate = document
    converter = LaTeXConverter()
    converter.ALLOWED_DIRS = [str(source.parent)]
    converter.pandoc_available = True
    converter.latexml_available = True
    calls = []

    def run(args, **kwargs):
        if args[1] in {"--version", "--VERSION"}:
            return SimpleNamespace(returncode=0, stdout="pandoc 3.1.11", stderr="")
        calls.append(args[0])
        Path(args[args.index("-o") + 1]).write_text(
            candidate.read_text().replace("<td>4</td>", "<td>0</td>")
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    receipts = {}
    assert converter.convert_to_html(str(source), conversion_receipts=receipts) is None
    assert calls == ["pandoc"]
    assert receipts["html"].status == "refused"
    assert "semantics_not_preserved" in {
        d.code for s in receipts["html"].stages for d in s.diagnostics
    }
