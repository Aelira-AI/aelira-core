"""Replay the bounded #444 corpus using an installed LuaLaTeX.

Run from the repo: python -m scripts.replay_latex_pdf_validation --output DIR
The JSON report records compiler/refusal behavior, not semantic accessibility.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from src.education.remediation.latex_converter import LaTeXConverter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not shutil.which("lualatex"):
        parser.error("LuaLaTeX must be installed for this explicit replay")
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1] / "tests/fixtures/latex_validation"
    manifest = json.loads((root / "corpus.json").read_text())
    version = subprocess.run(
        ["lualatex", "--version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    cases = []
    for case in manifest["cases"]:
        folder = Path(tempfile.mkdtemp(prefix=case["id"] + "-", dir=args.output))
        source = folder / "source.tex"
        data = (root / case["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == case["sha256"]
        source.write_bytes(data)
        converter = LaTeXConverter()
        converter.ALLOWED_DIRS = [str(folder.resolve())]
        raw = folder / "raw"
        raw.mkdir()
        candidate = converter._convert_with_lualatex(
            str(source.resolve()), raw.resolve()
        )
        compiled = bool(candidate)
        converter.lualatex_available = True
        converter.latexml_available = converter.pdflatex_available = False
        output, receipt = converter.convert_to_pdf_with_validation(
            str(source.resolve())
        )
        preserved = source.read_bytes() == data
        expected_reason = (
            "conversion_failed"
            if case["compile_expected"] == "failed"
            else "structure_check_failed"
        )
        passed = (
            compiled == (case["compile_expected"] == "passed")
            and output is None
            and receipt.reason == expected_reason
            and preserved
        )
        cases.append(
            {
                "id": case["id"],
                "source_sha256": case["sha256"],
                "compiler_produced_candidate": compiled,
                "source_preserved": preserved,
                "validation": receipt.model_dump(mode="json"),
                "expectation_met": passed,
            }
        )
    report = {"compiler": version, "scope": manifest["scope"], "cases": cases}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"{sum(row['expectation_met'] for row in cases)}/{len(cases)} expectations met"
    )
    raise SystemExit(0 if all(row["expectation_met"] for row in cases) else 1)


if __name__ == "__main__":
    main()
