#!/usr/bin/env python3
"""Authored exact-text corpus, independent oracles and runtime receipts."""

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import sys
import time

import pikepdf
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from pdf_font_fixtures import cmap_bytes, snapshot, truetype_pdf
from test_pdf_font_text import simple_pdf
from src.education.remediation.pdf_font_text import (
    FontTextBindingError,
    decode_page_text_runs,
)
from src.education.remediation.pdf_font_mapping_proposals import (
    AssignmentChange,
    ExpectedRun,
    FontMapPatch,
    FontMappingProposal,
    compile_font_mapping_proposal,
    inventory_sha256,
    plan_deterministic_font_maps,
)
from src.education.remediation.pdf_text_inventory import inspect_pdf_text_inventory

MANIFEST = ROOT / "tests/fixtures/pdf_font_recovery/manifest.json"
PINNED = {
    "pdfminer.six": "20260107",
    "pikepdf": "10.16.0",
    "PyMuPDF": "1.28.2",
    "fonttools": "4.66.0",
}


def fixture(case_id):
    factories = {
        "wrong-valid-numbers": lambda: simple_pdf(
            b"BT /F1 16 Tf 30 300 Td (ACCESSIBLE 105 - 1.5) Tj ET"
        ),
        "identity-unique": lambda: truetype_pdf(),
        "nonidentity-unique": lambda: truetype_pdf(gid_map=b"\0\0\0\2\0\1"),
        "inverse-alias": lambda: truetype_pdf(alias=True),
        "glyph-zero": lambda: truetype_pdf(gid_map=b"\0\0\0\0\0\2"),
        "partial-map": lambda: truetype_pdf(mappings={1: "A", 99: "retained"}),
        "wrong-valid-digit": lambda: truetype_pdf(
            mappings={1: "7", 2: "B", 99: "retained"}
        ),
        "supplementary": lambda: truetype_pdf(supplementary=True),
        "base-plus-differences": lambda: simple_pdf(
            b"BT /F1 16 Tf 30 300 Td <41427f> Tj ET",
            encoding=pikepdf.Dictionary(
                BaseEncoding=pikepdf.Name.WinAnsiEncoding,
                Differences=pikepdf.Array([127, pikepdf.Name.bullet]),
            ),
        ),
    }
    with factories[case_id]() as pdf:
        font = pdf.pages[0].Resources.Font.F1
        if not font.is_indirect:
            pdf.pages[0].Resources.Font.F1 = pdf.make_indirect(font)
        if case_id == "wrong-valid-numbers":
            mapping = {ord(char): char for char in "ACCESSIBLE 105 - 1.5"}
            mapping.update({65: "Z", 49: "7"})
            pdf.pages[0].Resources.Font.F1.ToUnicode = pdf.make_stream(
                cmap_bytes(mapping, width=1)
            )
        return snapshot(pdf)


def runtime():
    versions = {name: importlib.metadata.version(name) for name in PINNED}
    return {
        "versions": versions,
        "python": platform.python_version(),
        "platform": platform.machine(),
        "source_revision": os.environ.get("PDF_FIDELITY_REVISION"),
        "image_id": os.environ.get("PDF_FIDELITY_IMAGE_ID"),
        "render_settings": {"dpi": 72, "colorspace": "RGB", "alpha": False},
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "generator_sha256": hashlib.sha256(
            (ROOT / "tests/pdf_font_fixtures.py").read_bytes()
        ).hexdigest(),
    }


def render_pixels(source):
    with pymupdf.open(stream=source, filetype="pdf") as pdf:
        return tuple(
            hashlib.sha256(
                page.get_pixmap(dpi=72, colorspace=pymupdf.csRGB, alpha=False).samples
            ).hexdigest()
            for page in pdf
        )


def reviewed_fixture_plan(source, expected, operation):
    inventory = inspect_pdf_text_inventory(source)
    info = inventory.fonts[0]
    evidence = hashlib.sha256(
        b"authored fixture ground truth\0" + expected.encode()
    ).hexdigest()
    changes = (
        (AssignmentChange(2, None, "B", "trusted_authoring_text", evidence),)
        if operation == "supplement_partial"
        else (AssignmentChange(1, "7", "A", "trusted_authoring_text", evidence),)
    )
    if expected == "ACCESSIBLE 105 - 1.5":
        changes = tuple(
            AssignmentChange(code, old, new, "trusted_authoring_text", evidence)
            for code, old, new in [(65, "Z", "A"), (49, "7", "1")]
        )
    return FontMappingProposal(
        inventory.source_sha256,
        inventory_sha256(inventory),
        (
            FontMapPatch(
                info.identity,
                info.fingerprint,
                info.original_map_sha256,
                info.used_codes,
                operation,
                changes,
            ),
        ),
        tuple(
            ExpectedRun(run.page_index, run.start, run.end, expected)
            for run in inventory.runs
        ),
        "authored-corpus",
        "compiler-test-ground-truth-not-a-recognition-result",
    )


def evaluate_case(case):
    started = time.monotonic()
    source = fixture(case["id"])
    native, error = None, None
    try:
        with pikepdf.open(io.BytesIO(source)) as pdf:
            native = "".join(run.text for run in decode_page_text_runs(pdf, 0))
    except FontTextBindingError as failure:
        error = str(failure)
    expected = case["expected_text"]
    status = case["native"]
    passed = (
        (native == expected)
        if status == "resolved"
        else (
            (native is not None and native != expected)
            if status == "review_required"
            else error == "source_text_mapping_unavailable"
        )
    )
    compiled, pixel_preserved = None, None
    if case["id"] in {"partial-map", "wrong-valid-digit", "wrong-valid-numbers"}:
        operation = (
            "supplement_partial" if case["id"] == "partial-map" else "replace_reviewed"
        )
        plan = reviewed_fixture_plan(source, expected, operation)
    elif status == "resolved":
        plan = plan_deterministic_font_maps(source).proposal
    else:
        plan = None
    if plan is not None:
        result = compile_font_mapping_proposal(source, plan)
        with pikepdf.open(io.BytesIO(result.pdf_bytes)) as pdf:
            compiled = "".join(run.text for run in decode_page_text_runs(pdf, 0))
        pixel_preserved = render_pixels(source) == render_pixels(result.pdf_bytes)
        passed = passed and compiled == expected and pixel_preserved
    return {
        "id": case["id"],
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "expected_text": expected,
        "native_text": native,
        "native_refusal": error,
        "native_exact_match": native == expected,
        "compiled_text": compiled,
        "appearance_preserved": pixel_preserved,
        "fidelity_status": "unassessed",
        "oracle_conflict": native is not None and native != expected,
        "passed": passed,
        "seconds": time.monotonic() - started,
    }


def run_corpus():
    cases = json.loads(MANIFEST.read_text())["cases"]
    results = [evaluate_case(case) for case in cases]
    return {
        "schema_version": 1,
        "runtime": runtime(),
        "cases": results,
        "summary": {
            "case_count": len(results),
            "passed": sum(case["passed"] for case in results),
            "native_exact_matches": sum(case["native_exact_match"] for case in results),
            "native_abstentions": sum(case["native_text"] is None for case in results),
            "oracle_conflicts": sum(case["oracle_conflict"] for case in results),
            "incorrect_verified_fidelity_claims": sum(
                case["fidelity_status"] == "verified"
                and case["native_text"] != case["expected_text"]
                for case in results
            ),
        },
        "universal_fidelity_claim": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-pinned", action="store_true")
    args = parser.parse_args()
    receipt = run_corpus()
    encoded = (json.dumps(receipt, indent=2, ensure_ascii=True) + "\n").encode()
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(encoded)
    print(json.dumps(receipt["summary"], sort_keys=True))
    return (
        0
        if all(case["passed"] for case in receipt["cases"])
        and (not args.require_pinned or receipt["runtime"]["versions"] == PINNED)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
