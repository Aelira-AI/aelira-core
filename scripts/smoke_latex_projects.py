"""Real sandboxed converter controls for complete authored source projects."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup
from PIL import Image

from src.education.latex_project import inspect_archive, write_project, load_project
from src.education.latex_project_conversion import convert_project_html, _run_pandoc


def bundle(files):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def run():
    results = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        image_buffer = BytesIO()
        Image.new("RGB", (12, 8), "blue").save(image_buffer, format="PNG")
        image = image_buffer.getvalue()
        source = (
            r"\documentclass{college}\usepackage{equations}"
            r"\title{Project context}\author{Test Author}"
            r"\begin{document}\input{chapters/first part}\end{document}"
        )
        complete = {
            "paper/main.tex": source,
            "paper/college.cls": r"\ProvidesClass{college}\LoadClass{article}",
            "paper/equations.sty": r"\ProvidesPackage{equations}\newcommand{\energy}{E=mc^2}",
            "paper/chapters/first part.tex": r"First chapter. $\energy$ \input{second part}",
            "paper/chapters/second part.tex": "Second chapter kept in context.",
        }
        graphics = {
            "paper/main.tex": r"\documentclass{article}\usepackage{graphicx}\begin{document}\input{chapters/figure}\end{document}",
            "paper/chapters/figure.tex": r"\includegraphics[alt={A solid blue rectangle.}]{assets/blue rectangle.png}",
            "paper/chapters/assets/blue rectangle.png": image,
        }
        cases = [
            ("nested-preamble-macro", complete, "accepted", None),
            ("nested-authored-image", graphics, "accepted", None),
            (
                "missing-chapter",
                {
                    k: v
                    for k, v in complete.items()
                    if not k.endswith("second part.tex")
                },
                "refused",
                "dependencies_unresolved",
            ),
            (
                "missing-image",
                {k: v for k, v in graphics.items() if not k.endswith(".png")},
                "refused",
                "dependencies_unresolved",
            ),
            (
                "missing-local-style",
                {k: v for k, v in complete.items() if not k.endswith(".sty")},
                "refused",
                "dependencies_unresolved",
            ),
            (
                "missing-bibliography",
                {
                    "paper/main.tex": r"\documentclass{article}\begin{document}\bibliography{references}\end{document}"
                },
                "refused",
                "dependencies_unresolved",
            ),
            (
                "preserved-bibliography",
                {
                    "paper/main.tex": r"\documentclass{article}\begin{document}\cite{source}\bibliography{references}\end{document}",
                    "paper/references.bib": "@book{source,title={Authored reference}}",
                },
                "refused",
                "bibliography_export_unsupported",
            ),
        ]
        cases.append(
            (
                "unsupported-command",
                {
                    "paper/main.tex": r"\documentclass{article}\begin{document}Before. \unknown{MISSING_AUTHORED_CONTENT} After.\end{document}"
                },
                "refused",
                "unsupported_command",
            )
        )
        outside = root / "outside-secret.tex"
        outside.write_text("OUTSIDE_PROJECT_CONTROL_MUST_NOT_APPEAR", encoding="utf-8")
        cases.append(
            (
                "macro-generated-external-input",
                {
                    "paper/main.tex": r"\documentclass{article}\newcommand{\pull}{\input{"
                    + str(outside)
                    + r"}}\begin{document}\pull\end{document}"
                },
                "refused",
                "dependencies_unresolved",
            )
        )
        for name, files, expected, reason in cases:
            data = bundle(files)
            project = inspect_archive(data, "paper/main.tex")
            before = {
                path: sha256(content).hexdigest()
                for path, content in project.files.items()
            }
            owned = root / (name + "-original")
            write_project(project, owned)
            conversion = convert_project_html(project, root / (name + "-output"))
            assert conversion.provenance["status"] == expected, (
                name,
                conversion.provenance,
            )
            assert bool(conversion.path) == (expected == "accepted"), (
                name,
                conversion.issues,
            )
            assert conversion.provenance["archive_sha256"] == sha256(data).hexdigest()
            assert conversion.provenance["source_sha256"] == project.source_digest
            assert conversion.provenance["accessibility_status"] == "not_verified"
            assert conversion.provenance["human_review_required"] is True
            if reason:
                assert reason in conversion.provenance["reasons"], (
                    name,
                    conversion.provenance,
                )
            if conversion.path:
                output = Path(conversion.path).read_bytes()
                assert (
                    sha256(output).hexdigest() == conversion.provenance["output_sha256"]
                )
                assert "OUTSIDE_PROJECT_CONTROL_MUST_NOT_APPEAR" not in output.decode()
                soup = BeautifulSoup(output, "html.parser")
                if name == "nested-preamble-macro":
                    assert "First chapter." in soup.get_text()
                    assert "Second chapter kept in context." in soup.get_text()
                    assert soup.title.get_text() == "Project context"
                    assert soup.find("math") and soup.find("msup")
                    assert (
                        "E" in soup.find("math").get_text()
                        and "m" in soup.find("math").get_text()
                    )
                    assert "energy" not in soup.find("math").get_text()
                else:
                    assert soup.img["alt"] == "A solid blue rectangle."
                    import base64

                    assert base64.b64decode(soup.img["src"].split(",", 1)[1]) == image
            restored = load_project(owned)
            assert before == {
                path: sha256(content).hexdigest()
                for path, content in restored.files.items()
            }
            assert restored.manifest == project.manifest
            results.append(
                {
                    "case": name,
                    "status": conversion.provenance["status"],
                    "reasons": conversion.provenance["reasons"],
                    "archive_sha256": project.archive_digest,
                    "source_sha256": project.source_digest,
                    "output_sha256": conversion.provenance["output_sha256"],
                    "originals_unchanged": True,
                }
            )
        # Probe the converter boundary independently of dependency parsing:
        # only the input named on its command line is readable in sandbox mode.
        sandbox_source = root / "sandbox.tex"
        sandbox_source.write_text(
            r"\documentclass{article}\begin{document}SANDBOX_VISIBLE_CONTROL "
            + r"\input{"
            + str(outside)
            + r"}\end{document}"
        )
        sandbox_output, sandbox_log = root / "sandbox.html", root / "sandbox.log"
        code, error = _run_pandoc(sandbox_source, sandbox_output, sandbox_log)
        assert error is None and code == 0
        output = sandbox_output.read_text()
        assert "SANDBOX_VISIBLE_CONTROL" in output
        assert "OUTSIDE_PROJECT_CONTROL_MUST_NOT_APPEAR" not in output
        assert "Could not load include file" in sandbox_log.read_text()
        results.append(
            {
                "case": "sandbox-external-read",
                "status": "blocked",
                "positive_control": True,
            }
        )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    run()
