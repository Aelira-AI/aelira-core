#!/usr/bin/env python3
"""Local PDF text diagnostic; detailed glyph data is opt-in and never logged."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.education.remediation.pdf_text_inventory import inspect_pdf_text_inventory
from src.education.remediation.pdf_ocr_form import MAX_PDF_BYTES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--detailed-output",
        type=Path,
        help="Write sensitive glyph text and geometry to a new local JSON file",
    )
    args = parser.parse_args()
    with args.pdf.open("rb") as source:
        data = source.read(MAX_PDF_BYTES + 1)
    inventory = inspect_pdf_text_inventory(data)
    if args.detailed_output:
        # Exclusive creation prevents overwriting an existing review artifact.
        import os

        fd = os.open(args.detailed_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(asdict(inventory), output, ensure_ascii=True, indent=2)
            output.write("\n")
    print(json.dumps(inventory.summary(), sort_keys=True))
    return 0 if inventory.complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
