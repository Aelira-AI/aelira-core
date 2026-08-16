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


def main() -> None:
    pdf_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else str(
            Path(__file__).resolve().parent.parent
            / "tests"
            / "fixtures"
            / "pdfs"
            / "academic_paper.pdf"
        )
    )

    # AI options off: this runs fully offline. Turn generate_alt_text on when
    # an AI provider is configured (see docs/DEPENDENCIES.md) to draft alt
    # text for images while scanning.
    processor = PDFProcessor(generate_alt_text=False, enhance_descriptions=False)
    result = processor.process_pdf(pdf_path)

    print(f"File:             {pdf_path}")
    print(f"Compliance score: {result.compliance_score}")
    print(f"Issues found:     {len(result.issues)}")
    for issue in result.issues:
        page = issue.get("page_number")
        location = f" (page {page})" if page else ""
        print(
            f"  [{issue.get('severity')}] {issue.get('issue_type')}{location}: "
            f"{issue.get('message')}"
        )


if __name__ == "__main__":
    main()
