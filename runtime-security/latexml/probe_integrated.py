"""Read-only synthetic public-converter observations for the siunitx matrix."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types

from bs4 import BeautifulSoup
from probe_cases import CASES


def main() -> None:
    root = Path("/app")
    sys.path.insert(0, str(root))
    package = types.ModuleType("src.education.remediation")
    package.__path__ = [str(root / "src/education/remediation")]
    sys.modules[package.__name__] = package
    from src.education.remediation.latex_converter import LaTeXConverter

    converter = LaTeXConverter()
    rows = []
    with tempfile.TemporaryDirectory(prefix="aelira-siunitx-integrated-") as temp:
        for name, expression in CASES.items():
            if name in {"physics_then_siunitx", "siunitx_then_physics"}:
                continue
            directory = Path(temp) / name
            directory.mkdir()
            source = directory / "case.tex"
            source.write_text(
                "\\documentclass{article}\n\\usepackage{siunitx}\n"
                "\\begin{document}\n$" + expression + "$\n\\end{document}\n"
            )
            receipts = {}
            result = converter.convert_to_html(
                str(source), str(directory / "output"), conversion_receipts=receipts
            )
            receipt = receipts.get("html")
            math_tokens = []
            if result:
                soup = BeautifulSoup(Path(result).read_text(), "html.parser")
                math = soup.find("math")
                if math:
                    math_tokens = [
                        x.get_text().strip()
                        for x in math.find_all(["mn", "mi", "mo", "mtext"])
                    ]
            rows.append(
                {
                    "case": name,
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "accepted": bool(result),
                    "route": (
                        receipt.decision.selected_route
                        if receipt and receipt.decision
                        else None
                    ),
                    "receipt_status": receipt.status if receipt else None,
                    "diagnostic_codes": (
                        [d.code for stage in receipt.stages for d in stage.diagnostics]
                        if receipt
                        else []
                    ),
                    "math_tokens": math_tokens,
                }
            )
    print(
        json.dumps({"schema": "aelira-siunitx-integrated-v1", "rows": rows}, indent=2)
    )


if __name__ == "__main__":
    main()
