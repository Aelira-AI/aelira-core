"""Run the repository-authored PDF font corpus with exact text and paint checks."""

import hashlib
import io
import json
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from pdf_font_fixtures import cmap_bytes, snapshot, truetype_pdf
from test_pdf_font_text import simple_pdf
from src.education.remediation.pdf_font_mapping_proposals import (
    AssignmentChange,
    ExpectedRun,
    FontMapPatch,
    FontMappingProposal,
    compile_font_mapping_proposal,
    inventory_sha256,
    plan_deterministic_font_maps,
)
from src.education.remediation.pdf_font_text import (
    FontTextBindingError,
    decode_page_text_runs,
)
from src.education.remediation.pdf_text_inventory import inspect_pdf_text_inventory

pytestmark = pytest.mark.unit
MANIFEST = Path(__file__).parent / "fixtures/pdf_font_recovery/manifest.json"


def _fixture(case_id: str) -> bytes:
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


def _render(source: bytes) -> tuple[tuple[tuple[float, float], str], ...]:
    with pymupdf.open(stream=source, filetype="pdf") as pdf:
        return tuple(
            (
                (page.rect.width, page.rect.height),
                hashlib.sha256(
                    page.get_pixmap(
                        dpi=72, colorspace=pymupdf.csRGB, alpha=False
                    ).samples
                ).hexdigest(),
            )
            for page in pdf
        )


def _reviewed_plan(source: bytes, expected: str, operation: str):
    inventory = inspect_pdf_text_inventory(source)
    font = inventory.fonts[0]
    evidence = hashlib.sha256(
        b"authored fixture ground truth\0" + expected.encode()
    ).hexdigest()
    if expected == "ACCESSIBLE 105 - 1.5":
        changes = [(65, "Z", "A"), (49, "7", "1")]
    elif operation == "supplement_partial":
        changes = [(2, None, "B")]
    else:
        changes = [(1, "7", "A")]
    return FontMappingProposal(
        inventory.source_sha256,
        inventory_sha256(inventory),
        (
            FontMapPatch(
                font.identity,
                font.fingerprint,
                font.original_map_sha256,
                font.used_codes,
                operation,
                tuple(
                    AssignmentChange(code, old, new, "trusted_authoring_text", evidence)
                    for code, old, new in changes
                ),
            ),
        ),
        tuple(
            ExpectedRun(run.page_index, run.start, run.end, expected)
            for run in inventory.runs
        ),
        "authored-corpus",
        "synthetic-ground-truth-not-recognition",
    )


def test_authored_font_corpus_exact_text_abstention_and_saved_paint():
    cases = json.loads(MANIFEST.read_text())["cases"]
    assert len(cases) == 9
    assert len({case["id"] for case in cases}) == len(cases)
    results = {}
    for case in cases:
        source = _fixture(case["id"])
        source_hash = hashlib.sha256(source).hexdigest()
        expected = case["expected_text"]
        native, refusal = None, None
        try:
            with pikepdf.open(io.BytesIO(source)) as pdf:
                native = "".join(run.text for run in decode_page_text_runs(pdf, 0))
        except FontTextBindingError as exc:
            refusal = str(exc)
        if case["native"] == "resolved":
            assert native == expected, case["id"]
        elif case["native"] == "mapping_unavailable":
            assert native is None and refusal == "source_text_mapping_unavailable"
        else:
            assert case["native"] == "review_required"
            assert native is not None and native != expected

        if case["id"] in {"partial-map", "wrong-valid-digit", "wrong-valid-numbers"}:
            operation = (
                "supplement_partial"
                if case["id"] == "partial-map"
                else "replace_reviewed"
            )
            plan = _reviewed_plan(source, expected, operation)
        elif case["native"] == "resolved":
            plan = plan_deterministic_font_maps(source).proposal
        else:
            plan = None
        if plan is not None:
            compiled = compile_font_mapping_proposal(source, plan)
            with pikepdf.open(io.BytesIO(compiled.pdf_bytes)) as pdf:
                saved = "".join(run.text for run in decode_page_text_runs(pdf, 0))
            assert saved == expected, case["id"]
            assert _render(source) == _render(compiled.pdf_bytes), case["id"]
            assert compiled.fidelity_status == "unassessed"
            assert compiled.source_sha256 == source_hash
        assert hashlib.sha256(source).hexdigest() == source_hash
        results[case["id"]] = (native, expected)
    assert results["wrong-valid-digit"] == ("7B", "AB")
    assert results["wrong-valid-numbers"][0] != results["wrong-valid-numbers"][1]
