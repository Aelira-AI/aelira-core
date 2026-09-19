"""Real converter controls for authored relationships, not conformance."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw

from src.education.remediation.latex_converter import LaTeXConverter
from src.education.latex_semantics import verify_html_semantics
from src.education.latex_diagnostics import conversion_session


def run():
    converter = LaTeXConverter()
    results = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        # Deliberately synthetic directed graph: A -> B -> C. The authored
        # alternative names both edges, so losing either falsifies the control.
        diagram = Image.new("RGB", (300, 100), "white")
        draw = ImageDraw.Draw(diagram)
        for index, label in enumerate("ABC"):
            x = 40 + index * 100
            draw.ellipse((x - 15, 35, x + 15, 65), outline="black", width=2)
            draw.text((x - 3, 43), label, fill="black")
            if index < 2:
                draw.line((x + 15, 50, x + 80, 50), fill="black", width=2)
                draw.polygon([(x + 80, 50), (x + 70, 45), (x + 70, 55)], fill="black")
        diagram.save(root / "diagram.png")
        table = r"""\begin{tabular}{lll}
Trial & A & B \\
Red & 1 & 2 \\
Blue & 3 & 4 \\
\end{tabular}"""
        for name, body, expected in [
            (
                "directed-edges",
                r"\includegraphics[alt={A points to B. B points to C.}]{diagram.png}",
                True,
            ),
            ("decorative", r"\includegraphics[artifact]{diagram.png}", True),
            (
                "caption-only",
                r"\begin{figure}\includegraphics{diagram.png}\caption{A graph}\end{figure}",
                False,
            ),
            (
                "blue-b-trial",
                r"\tagpdfsetup{table/header-rows={1},table/header-columns={1}}" + table,
                True,
            ),
            ("visual-rule-only", table.replace("Trial", r"\hline Trial"), False),
        ]:
            source = root / (name + ".tex")
            source.write_text(
                "\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n"
                + body
                + "\n\\end{document}\n"
            )
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            receipts = {}
            candidate = converter.convert_to_html(
                str(source), conversion_receipts=receipts
            )
            reasons = [
                d.code
                for s in receipts["html"].stages
                for d in s.diagnostics
                if d.severity == "error"
            ]
            assert bool(candidate) == expected, (name, reasons)
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            if candidate:
                assert verify_html_semantics(source, Path(candidate))
                soup = BeautifulSoup(Path(candidate).read_text(), "html.parser")
                if name == "directed-edges":
                    assert soup.img["alt"] == "A points to B. B points to C."
                if name == "blue-b-trial":
                    cell = soup.find("td", string="4")
                    assert [soup.find(id=h).get_text() for h in cell["headers"]] == [
                        "B",
                        "Blue",
                    ]
                    blue = soup.find("th", string="Blue")
                    assert [soup.find(id=h).get_text() for h in blue["headers"]] == [
                        "Trial"
                    ]
            results.append(
                {
                    "case": name,
                    "status": "accepted" if candidate else "refused",
                    "reasons": reasons,
                    "source_unchanged": True,
                }
            )
        # A real PDF with the source graphic is still withheld: compilation
        # and generic tagging checks do not prove source-to-structure identity.
        source = root / "directed-edges.tex"
        pdf_dir = root / "pdf-control"
        pdf_dir.mkdir()
        with conversion_session() as stages:
            pdf = converter._convert_with_pdflatex(str(source), pdf_dir)
            assert pdf, "PDF compilation control failed"
            assert not converter._preserve_semantics(source, Path(pdf), "pdf")
        assert "semantics_unsupported" in {
            d.code for s in stages for d in s.diagnostics
        }
        results.append(
            {
                "case": "pdf-relationships",
                "compiled": True,
                "status": "refused",
                "reason": "semantics_unsupported",
            }
        )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    run()
