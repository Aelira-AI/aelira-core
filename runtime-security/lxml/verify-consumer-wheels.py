"""Check every frozen consumer wheel against captured public PyPI metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def main() -> None:
    root = Path(sys.argv[1])
    rows = json.loads((root / "pypi-metadata.json").read_text())
    assert len(rows) == 8
    expected = {
        "lxml": "6.1.3",
        "python-docx": "1.2.0",
        "python-pptx": "1.0.2",
        "openpyxl": "3.1.5",
        "pillow": "12.3.0",
        "XlsxWriter": "3.2.9",
        "typing_extensions": "4.15.0",
        "et_xmlfile": "2.0.0",
    }
    assert {row["package"]: row["version"] for row in rows} == expected
    assert {path.name for path in root.glob("*.whl")} == {
        row["filename"] for row in rows
    }
    for row in rows:
        path = root / row["filename"]
        assert (
            path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        )
        assert row["url"].startswith("https://files.pythonhosted.org/")
        assert (
            row["metadata_url"]
            == f"https://pypi.org/pypi/{row['package']}/{row['version']}/json"
        )
    print(
        json.dumps(
            {"checked": len(rows), "wheels": {r["package"]: r["sha256"] for r in rows}},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
