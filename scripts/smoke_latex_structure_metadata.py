"""Required real-compiler metadata checks; no PDF/UA conformance claim."""

import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pikepdf

from src.education.latex_runtime import tex_environment
from src.education.remediation.base import RemediationConfig
from src.education.remediation.latex_converter import LaTeXConverter
from src.education.remediation.latex_remediator import LatexRemediator


def verify(root):
    converter = LaTeXConverter()
    assert converter.lualatex_available, "LuaLaTeX is required for this CI check"
    rows = []
    for name, declarations in (
        ("title-author", r"\title{Grüße, Welt}\author{Test Author}"),
        (
            "hypersetup",
            r"\RequirePackage{hyperref}\hypersetup{pdftitle={Grüße, Welt},pdfauthor={Test Author}}",
        ),
    ):
        folder = root / name
        folder.mkdir()
        original = (
            "\\documentclass{article}\n\\usepackage[ngerman]{babel}\n"
            + declarations
            + "\n\\begin{document}Ein Text.\\end{document}"
        )
        source = folder / "source.tex"
        source.write_text(original)
        remediator = LatexRemediator(str(source), [], RemediationConfig(use_ai=False))
        remediator._load_document()
        assert remediator._apply_structure_fix("accessibility")
        candidate = folder / "candidate.tex"
        candidate.write_text(remediator._modified_content)
        # Test the generated source with the real compiler. Application
        # diagnostics and publication acceptance are separate checks.
        for _ in range(2):
            compiled = subprocess.run(
                [
                    "lualatex",
                    "--no-shell-escape",
                    "--interaction=nonstopmode",
                    "--halt-on-error",
                    candidate.name,
                ],
                cwd=folder,
                env=tex_environment(folder),
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        pdf_path = candidate.with_suffix(".pdf")
        compiled_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        with pikepdf.open(pdf_path) as pdf:
            assert pdf.pdf_version == "1.7"
            assert pdf.Root.Lang == "de"
            assert pdf.docinfo.Title == "Grüße, Welt"
            assert pdf.docinfo.Author == "Test Author"
        # Older TeX runtimes split XMP titles at commas. Verify the existing
        # production normalization pass as well as the raw compiler PDF Info.
        assert converter._preserve_metadata(candidate, pdf_path, "pdf")
        with pikepdf.open(pdf_path) as pdf:
            assert pdf.pdf_version == "1.7"
            assert pdf.Root.Lang == "de"
            assert pdf.docinfo.Title == "Grüße, Welt"
            assert pdf.docinfo.Author == "Test Author"
            with pdf.open_metadata() as xmp:
                assert xmp["dc:title"] == "Grüße, Welt"
                assert xmp["dc:creator"] == ["Test Author"]
                assert xmp["pdfuaid:part"] == "1"
        assert source.read_text() == original
        rows.append(
            {
                "case": name,
                "status": "passed",
                "compiler_pdf_sha256": compiled_hash,
                "normalized_pdf_sha256": hashlib.sha256(
                    pdf_path.read_bytes()
                ).hexdigest(),
                "accessibility": "not_assessed",
            }
        )
    return rows


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="aelira-structure-metadata-") as temp:
        print(json.dumps(verify(Path(temp)), indent=2))
