#!/usr/bin/env python3
"""Offline font-map proposals and compilation with exclusive output creation."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.education.remediation.pdf_font_mapping_proposals import (
    MAX_PLAN_BYTES,
    compile_font_mapping_proposal,
    plan_deterministic_font_maps,
    proposal_from_json,
    proposal_to_json,
)
from src.education.remediation.pdf_ocr_form import MAX_PDF_BYTES


def write_new(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--reviewed-plan",
        type=Path,
        help="Compile a separately reviewed v2 proposal; the caller establishes actor authority",
    )
    args = parser.parse_args()
    with args.source.open("rb") as source:
        data = source.read(MAX_PDF_BYTES + 1)
    if args.reviewed_plan:
        with args.reviewed_plan.open("rb") as proposed:
            plan = proposal_from_json(proposed.read(MAX_PLAN_BYTES + 1))
        result = compile_font_mapping_proposal(data, plan)
        write_new(args.output, result.pdf_bytes)
        print(
            json.dumps(
                {
                    "source_sha256": result.source_sha256,
                    "output_sha256": result.output_sha256,
                    "proposal_sha256": result.proposal_sha256,
                    "operation_counts": result.operation_counts,
                    "saved_consistency_verified": result.saved_consistency_verified,
                    "independent_review_pending": result.independent_review_pending,
                    "fidelity_status": result.fidelity_status,
                },
                sort_keys=True,
            )
        )
    else:
        result = plan_deterministic_font_maps(data)
        if result.proposal is None:
            print(
                json.dumps(
                    {
                        "proposal_created": False,
                        "unresolved_font_count": len(result.unresolved_fonts),
                    }
                )
            )
            return 2 if result.unresolved_fonts else 0
        write_new(args.output, proposal_to_json(result.proposal))
        print(
            json.dumps(
                {
                    "proposal_created": True,
                    "font_count": len(result.proposal.fonts),
                    "independent_review_pending": True,
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
