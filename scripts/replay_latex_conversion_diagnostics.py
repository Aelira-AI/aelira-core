"""Replay known-loss controls through installed HTML converter wrappers.

Run: python -m scripts.replay_latex_conversion_diagnostics --engine pandoc --output DIR
This is a bounded loss check, not semantic equivalence or accessibility evidence.
"""

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from src.education.latex_diagnostics import sha
from src.education.remediation.latex_converter import LaTeXConverter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["pandoc", "latexml"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tools = [args.engine] + (["latexmlpost"] if args.engine == "latexml" else [])
    if not all(shutil.which(tool) for tool in tools):
        parser.error("Requested converter tools must be installed")
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1] / "tests/fixtures/latex_validation"
    manifest = json.loads((root / "corpus.json").read_text())
    cases = []
    for case in manifest["cases"]:
        folder = Path(
            tempfile.mkdtemp(prefix=case["id"] + "-", dir=args.output)
        ).resolve()
        source = folder / "source.tex"
        data = (root / case["file"]).read_bytes()
        if sha(data) != case["sha256"]:
            raise ValueError("Corpus source hash mismatch")
        source.write_bytes(data)
        converter = LaTeXConverter()
        converter.ALLOWED_DIRS = [str(folder)]
        converter.latexml_available = args.engine == "latexml"
        converter.pandoc_available = args.engine == "pandoc"
        receipts = {}
        output = converter.convert_to_html(str(source), conversion_receipts=receipts)
        expected = not case["id"].startswith("N") and not (
            args.engine == "pandoc" and case["id"] in {"M10", "M14"}
        )
        cases.append(
            {
                "id": case["id"],
                "source_sha256": case["sha256"],
                "source_preserved": source.read_bytes() == data,
                "returned_candidate": bool(output),
                "expectation_met": bool(output) == expected
                and source.read_bytes() == data,
                "diagnostics": receipts["html"].model_dump(mode="json"),
            }
        )
    report = {"engine": args.engine, "scope": "known-loss-checks-only", "cases": cases}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    passed = sum(row["expectation_met"] for row in cases)
    print(f"{passed}/{len(cases)} wrapper expectations met")
    raise SystemExit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
