"""Check every frozen consumer wheel against captured public PyPI metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


EXPECTED_WHEEL_DIGESTS = {
    "amd64": {
        "lxml-6.1.3-cp314-cp314-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl": "a7eb78ba28b187e1e9203a55c60fcf70df2d22cb205fe6d51b9383d6097419f0",
        "python_docx-1.2.0-py3-none-any.whl": "3fd478f3250fbbbfd3b94fe1e985955737c145627498896a8a6bf81f4baf66c7",
        "python_pptx-1.0.2-py3-none-any.whl": "160838e0b8565a8b1f67947675886e9fea18aa5e795db7ae531606d68e785cba",
        "openpyxl-3.1.5-py2.py3-none-any.whl": "5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2",
        "pillow-13.0.0-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl": "e91c2b1cbeae768c98752e67e717e283d68dae45717b41f2d91eaee648e37b7d",
        "xlsxwriter-3.2.9-py3-none-any.whl": "9a5db42bc5dff014806c58a20b9eae7322a134abb6fce3c92c181bfb275ec5b3",
        "typing_extensions-4.15.0-py3-none-any.whl": "f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548",
        "et_xmlfile-2.0.0-py3-none-any.whl": "7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa",
    },
    "arm64": {
        "lxml-6.1.3-cp314-cp314-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl": "2221e88679d1351e9a40aaee54bc65679b9795bbd0160bc3d5e36b163344eb75",
        "python_docx-1.2.0-py3-none-any.whl": "3fd478f3250fbbbfd3b94fe1e985955737c145627498896a8a6bf81f4baf66c7",
        "python_pptx-1.0.2-py3-none-any.whl": "160838e0b8565a8b1f67947675886e9fea18aa5e795db7ae531606d68e785cba",
        "openpyxl-3.1.5-py2.py3-none-any.whl": "5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2",
        "pillow-13.0.0-cp314-cp314-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl": "5933fcea5a5ba64c686f55c886a115cbfd92f2900460784a1e79909fdbafd7b6",
        "xlsxwriter-3.2.9-py3-none-any.whl": "9a5db42bc5dff014806c58a20b9eae7322a134abb6fce3c92c181bfb275ec5b3",
        "typing_extensions-4.15.0-py3-none-any.whl": "f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548",
        "et_xmlfile-2.0.0-py3-none-any.whl": "7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa",
    },
}


def main() -> None:
    root = Path(sys.argv[1])
    rows = json.loads((root / "pypi-metadata.json").read_text())
    assert len(rows) == 8
    expected = {
        "lxml": "6.1.3",
        "python-docx": "1.2.0",
        "python-pptx": "1.0.2",
        "openpyxl": "3.1.5",
        "pillow": "13.0.0",
        "XlsxWriter": "3.2.9",
        "typing_extensions": "4.15.0",
        "et_xmlfile": "2.0.0",
    }
    assert {row["package"]: row["version"] for row in rows} == expected
    assert {path.name for path in root.glob("*.whl")} == {
        row["filename"] for row in rows
    }
    arch = "arm64" if any("aarch64" in row["filename"] for row in rows) else "amd64"
    expected_digests = EXPECTED_WHEEL_DIGESTS[arch]
    for row in rows:
        path = root / row["filename"]
        expected_digest = expected_digests[row["filename"]]
        assert row["sha256"] == expected_digest
        assert (
            path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == expected_digest
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
