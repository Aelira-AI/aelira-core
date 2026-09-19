"""Language metadata must not introduce an undefined hyperref command."""

import re

import pytest

from src.education.remediation.base import RemediationConfig
from src.education.remediation.latex_remediator import LatexRemediator


@pytest.mark.parametrize(
    "packages",
    [
        "",
        "% \\usepackage{hyperref}\n",
        "\\usepackage{hyperref}\n",
        "\\usepackage[hidelinks]{hyperref}\n",
        "\\usepackage{amsmath,hyperref}\n",
        "\\RequirePackage [hidelinks] {hyperref}\n",
    ],
)
def test_language_metadata_loads_dependency_once_without_changing_options(
    tmp_path, packages
):
    source = (
        "\\documentclass{article}\n"
        + packages
        + ("\\begin{document}\nAuthored equation: $x^{a_b}$.\n\\end{document}\n")
    )
    path = tmp_path / "source.tex"
    path.write_text(source)
    remediator = LatexRemediator(str(path), [], RemediationConfig(use_ai=False))
    remediator._load_document()
    assert remediator._apply_language_fix("english")
    output = remediator._modified_content
    # Repeating the repair cannot introduce another package load or metadata block.
    assert remediator._apply_language_fix("english")
    assert remediator._modified_content == output
    commands = re.sub(r"(?<!\\)%[^\n]*", "", output)
    loads = list(
        re.finditer(
            r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}",
            commands,
        )
    )
    hyperref = [
        m for m in loads if "hyperref" in m.group(1).replace(" ", "").split(",")
    ]
    assert len(hyperref) == 1
    assert hyperref[0].end() < commands.index(r"\hypersetup")
    assert commands.index(r"\hypersetup") < commands.index(r"\begin{document}")
    assert packages in output
    assert (
        output.split(r"\begin{document}", 1)[1]
        == source.split(r"\begin{document}", 1)[1]
    )
    assert path.read_text() == source
