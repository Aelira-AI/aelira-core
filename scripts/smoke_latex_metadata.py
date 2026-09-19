"""Synthetic saved-metadata controls; no accessibility conformance claim."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.education.remediation.latex_converter import LaTeXConverter
from src.education.latex_diagnostics import conversion_session
from src.education.latex_metadata import extract_metadata, save_pdf_metadata
from bs4 import BeautifulSoup
import pikepdf
import pymupdf

converter = LaTeXConverter()
rows = []
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    for name, preamble, body in [
        (
            "german",
            r"\usepackage[ngerman]{babel}\title{Grüße}\author{Test Author}",
            "Deutsch.",
        ),
        (
            "english",
            r"\usepackage[english]{babel}\title{Hello}\author{Test Author}",
            "English.",
        ),
        ("unknown", "", "Unknown metadata."),
        (
            "mixed",
            r"\usepackage[english,main=ngerman]{babel}",
            r"Deutsch \foreignlanguage{english}{Hello}.",
        ),
        (
            "ambiguous",
            r"\usepackage[ngerman]{babel}\hypersetup{pdflang=en}",
            "Conflict.",
        ),
    ]:
        source = root / (name + ".tex")
        source.write_text(
            r"\documentclass{article}"
            + "\n"
            + preamble
            + "\n"
            + r"\begin{document}"
            + body
            + " $x^2+1$."
            + r"\end{document}"
        )
        for engine in ("latexml", "pandoc"):
            out = root / (name + "-" + engine)
            out.mkdir()
            with conversion_session() as stages:
                result = getattr(converter, "_convert_with_" + engine)(str(source), out)
            reasons = [
                d.code for s in stages for d in s.diagnostics if d.severity == "error"
            ]
            if name in ("german", "english", "unknown"):

                assert result, (name, engine, reasons)
                soup = BeautifulSoup(Path(result).read_text(), "html.parser")
                m = extract_metadata(source.read_text())
                assert soup.html.get("lang") == m.language
                assert (soup.title.get_text() if soup.title else None) == m.title
            if name == "ambiguous":
                assert not result
            if name == "mixed":
                assert bool(result) == (engine == "pandoc")
            if name == "mixed" and result:
                soup = BeautifulSoup(Path(result).read_text(), "html.parser")
                assert any(
                    tag.get_text() == "Hello" for tag in soup.find_all(lang="en")
                )
            rows.append(
                {
                    "case": name,
                    "engine": engine,
                    "status": "accepted" if result else "refused",
                    "reasons": reasons,
                }
            )
        if name in ("german", "english", "unknown"):
            out = root / (name + "-pdf")
            out.mkdir()
            pdf = converter._convert_with_lualatex(str(source), out)

            assert pdf, (name, "compile")
            assert save_pdf_metadata(Path(pdf), extract_metadata(source.read_text()))
            with pikepdf.open(pdf) as saved:
                assert (
                    saved.Root.get("/Lang")
                    == extract_metadata(source.read_text()).language
                )
            rows.append(
                {
                    "case": name,
                    "engine": "lualatex",
                    "metadata": "verified",
                    "accessibility": "not_assessed",
                }
            )
    # Moving compilation to owned scratch must retain relative source lookup.
    (root / "included-text.tex").write_text("Included text survives.")
    lookup = root / "lookup.tex"
    lookup.write_text(
        r"\documentclass{article}\begin{document}\input{included-text}\end{document}"
    )
    output = root / "lookup-output"
    output.mkdir()
    candidate = converter._convert_with_lualatex(str(lookup), output)
    assert candidate
    with pymupdf.open(candidate) as document:
        assert "Included text survives." in document[0].get_text()
    rows.append(
        {"case": "relative-input", "engine": "lualatex", "source_lookup": "verified"}
    )
print(json.dumps(rows, indent=2))
