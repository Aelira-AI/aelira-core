"""Bounded synthetic siunitx compatibility probe for the temporary ARM image.

This reports observations, never a compatibility score or an accessibility claim.
Run without network, as a non-root user, with a read-only root and tmpfs /tmp.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

CASES = {
    "legacy_si": r"\SI{3.00e8}{\metre\per\second}",
    "v3_qty": r"\qty{3.00e8}{\metre\per\second}",
    "legacy_si_optional": r"\SI[per-mode=fraction]{3}{\metre\per\second}",
    "legacy_si_between_brackets": r"\SI{3}[pre]{\metre}",
    "v3_qty_optional": r"\qty[per-mode=fraction]{3}{\metre\per\second}",
    "v3_qty_rounding": r"\qty[round-mode=places,round-precision=1]{1.234}{\metre}",
    "v3_qty_global_rounding": r"\sisetup{round-mode=places,round-precision=1}\qty{1.234}{\metre}",
    "v3_qty_decimal_option": r"\qty[output-decimal-marker={,}]{1.23}{\metre}",
    "v3_qty_uncertainty": r"\qty{1.23(4)e5}{\milli\metre\per\second\squared}",
    "v3_qty_prefix": r"\qty{2.5}{\kilo\metre\per\second}",
    "v3_qtylist": r"\qtylist{1;2;3}{\metre}",
    "v3_qtylist_single": r"\qtylist[list-units=single]{1;2;3}{\metre}",
    "v3_qtyrange": r"\qtyrange{1}{2}{\metre}",
    "v3_qtyrange_single": r"\qtyrange[range-units=single]{1}{2}{\metre}",
    "v3_unit": r"\unit{\metre\per\second}",
    "v3_unit_fraction": r"\unit[per-mode=fraction]{\metre\per\second}",
    "mixed_legacy_v3": r"\SI{1}{\metre} + \qty{2}{\metre}",
    "physics_then_siunitx": r"\qty(\frac{1}{2}) + \SI{3}{\metre}",
    "siunitx_then_physics": r"\qty(\frac{1}{2}) + \SI{3}{\metre}",
    "invalid_number": r"\qty{not-a-number}{\metre}",
}


def run(args: list[str], directory: Path) -> dict:
    try:
        result = subprocess.run(
            args,
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        lines = [
            line.strip()[:500]
            for line in (result.stdout + "\n" + result.stderr).splitlines()
            if any(
                word in line.lower()
                for word in ("error", "warning", "fatal", "undefined")
            )
        ]
        return {
            "exit_code": result.returncode,
            "diagnostics": lines[:20],
            "timeout": False,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "diagnostics": [], "timeout": True}


def main() -> None:
    rows = []
    with tempfile.TemporaryDirectory(prefix="aelira-siunitx-probe-") as path:
        root = Path(path)
        for name, expression in CASES.items():
            directory = root / name
            directory.mkdir()
            source = directory / "case.tex"
            packages = (
                "\\usepackage{physics}\n\\usepackage{siunitx}\n"
                if name == "physics_then_siunitx"
                else (
                    "\\usepackage{siunitx}\n\\usepackage{physics}\n"
                    if name == "siunitx_then_physics"
                    else "\\usepackage{siunitx}\n"
                )
            )
            source.write_text(
                "\\documentclass{article}\n"
                + packages
                + "\\begin{document}\n$"
                + expression
                + "$\n\\end{document}\n"
            )
            xml = directory / "case.xml"
            html = directory / "case.html"
            parse = run(
                [
                    "latexml",
                    f"--log={directory / 'parse.log'}",
                    f"--destination={xml}",
                    str(source),
                ],
                directory,
            )
            post = (
                run(
                    [
                        "latexmlpost",
                        "--format=html5",
                        f"--destination={html}",
                        str(xml),
                    ],
                    directory,
                )
                if xml.exists() and not parse["timeout"]
                else None
            )
            error_nodes = []
            xmath = []
            if xml.exists():
                try:
                    tree = ET.parse(xml)
                    ns = "{http://dlmf.nist.gov/LaTeXML}"
                    error_nodes = [
                        "".join(node.itertext())[:300]
                        for node in tree.findall(f".//{ns}ERROR")
                    ]
                    xmath = [
                        {
                            "text": "".join(node.itertext())[:300],
                            "meaning": node.get("meaning"),
                        }
                        for node in tree.findall(f".//{ns}XMath")
                    ]
                except ET.ParseError:
                    error_nodes = ["malformed XML"]
            math_tokens = []
            math_markup = None
            if html.exists():
                soup = BeautifulSoup(html.read_text(), "html.parser")
                math = soup.find("math")
                if math:
                    math_tokens = [
                        x.get_text().strip()
                        for x in math.find_all(["mn", "mi", "mo", "mtext"])
                    ]
                    math_markup = str(math)[:6000]
            rows.append(
                {
                    "case": name,
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "expression": expression,
                    "parse": parse,
                    "post": post,
                    "error_nodes": error_nodes,
                    "xmath": xmath,
                    "math_tokens": math_tokens,
                    "math_markup": math_markup,
                }
            )
    print(
        json.dumps(
            {
                "schema": "aelira-siunitx-v3-probe-v1",
                "timeout_seconds_per_stage": 15,
                "rows": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
