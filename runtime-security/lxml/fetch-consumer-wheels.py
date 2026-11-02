"""Freeze public PyPI wheel bytes and metadata for actual Office libraries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import urllib.request


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
    arch = sys.argv[2]
    assert arch in {"arm64", "amd64"}
    expected_digests = EXPECTED_WHEEL_DIGESTS[arch]
    directory = root / "consumer-wheels"
    directory.mkdir()
    packages = [
        ("lxml", "6.1.3"),
        ("python-docx", "1.2.0"),
        ("python-pptx", "1.0.2"),
        ("openpyxl", "3.1.5"),
        ("pillow", "13.0.0"),
        ("XlsxWriter", "3.2.9"),
        ("typing_extensions", "4.15.0"),
        ("et_xmlfile", "2.0.0"),
    ]
    rows = []
    for package, version in packages:
        url = f"https://pypi.org/pypi/{package}/{version}/json"
        metadata = json.load(urllib.request.urlopen(url, timeout=30))
        assert metadata["info"]["version"] == version
        wheels = [
            item for item in metadata["urls"] if item["filename"].endswith(".whl")
        ]
        if package in {"lxml", "pillow"}:
            machine = "aarch64" if arch == "arm64" else "x86_64"
            wheels = [
                item
                for item in wheels
                if "cp314-cp314-manylinux" in item["filename"]
                and machine in item["filename"]
            ]
            if package == "lxml":
                # PyPI also publishes a compatible manylinux2014 wheel.
                # Select the newer glibc target explicitly for this Ubuntu base.
                wheels = [
                    item for item in wheels if "manylinux_2_26_" in item["filename"]
                ]
        else:
            wheels = [
                item for item in wheels if item["filename"].endswith("none-any.whl")
            ]
        assert len(wheels) == 1, (package, [item["filename"] for item in wheels])
        item = wheels[0]
        filename = item["filename"]
        assert filename in expected_digests, f"Unexpected or unpinned wheel: {filename}"
        expected_digest = expected_digests[filename]
        assert item["digests"]["sha256"] == expected_digest, (
            f"PyPI reported digest {item['digests']['sha256']} differs from committed {expected_digest}"
        )
        data = urllib.request.urlopen(item["url"], timeout=90).read()
        digest = hashlib.sha256(data).hexdigest()
        assert digest == expected_digest, (
            f"Downloaded wheel digest {digest} differs from committed {expected_digest}"
        )
        assert digest == item["digests"]["sha256"]
        (directory / item["filename"]).write_bytes(data)
        rows.append(
            {
                "package": package,
                "version": version,
                "filename": item["filename"],
                "sha256": item["digests"]["sha256"],
                "url": item["url"],
                "metadata_url": url,
            }
        )
    (directory / "pypi-metadata.json").write_text(
        json.dumps(rows, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps({"architecture": arch, "wheels": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
