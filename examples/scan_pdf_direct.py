"""Scan a PDF for accessibility issues using the engine directly — no API server.

The processors are plain Python classes: you can embed them in your own
pipeline (a nightly batch job, a CI check on course materials, a migration
script) without running the FastAPI app at all.

Usage, from the repository root with dependencies installed. The settings
module insists on DATABASE_URL and JWT_SECRET even though this example never
touches the database — point them at anything:

    DATABASE_URL=postgresql://localhost/unused JWT_SECRET=dev-only \
        python examples/scan_pdf_direct.py path/to/document.pdf

With no argument it scans one of the repository's test fixtures, so it runs
out of the box.
"""

import sys
from pathlib import Path

# Run from the repository root (the src package must be importable).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.education.pdf_processor import PDFProcessor
from src.education.scan_completeness import (
    IncompleteScanError,
    public_scan_failure_message,
)


def main() -> int:
    pdf_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else str(
            Path(__file__).resolve().parent.parent
            / "tests"
            / "fixtures"
            / "pdfs"
            / "simple_syllabus.pdf"
        )
    )

    # AI options off: this runs fully offline. Turn generate_alt_text on when
    # an AI provider is configured (see docs/DEPENDENCIES.md) to draft alt
    # text for images while scanning.
    processor = PDFProcessor(generate_alt_text=False, enhance_descriptions=False)
    try:
        result = processor.process_pdf(pdf_path)
    except IncompleteScanError as error:
        print(f"Scan incomplete: {public_scan_failure_message(error)}", file=sys.stderr)
        return 1

    print(f"File:             {pdf_path}")
    print(f"Compliance score: {result.compliance_score}")
    print(f"Issues found:     {len(result.issues)}")
    for issue in result.issues:
        page = issue.get("page_number")
        location = f" (page {page})" if page else ""
        identifier = (
            issue.get("issue_type")
            or issue.get("type")
            or issue.get("rule_id")
            or "accessibility_issue"
        )
        print(
            f"  [{issue.get('severity')}] {identifier}{location}: "
            f"{issue.get('message')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
