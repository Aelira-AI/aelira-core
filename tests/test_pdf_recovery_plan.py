"""Strict private transport for supplied font and semantic reviews."""

from dataclasses import FrozenInstanceError, replace
import json

import pytest

from src.education.remediation import pdf_recovery_plan as module
from src.education.remediation.pdf_recovery_plan import (
    PDFRecoveryPlanError,
    ReviewedPDFRecovery,
    deserialize_reviewed_pdf_recovery,
    reviewed_pdf_recovery_receipt,
    serialize_reviewed_pdf_recovery,
)
from src.education.remediation.pdf_reviewed_semantics import (
    ReviewedOccurrence,
    ReviewedSemanticManifest,
    ReviewedSemanticNode,
)
from src.education.remediation.pdf_verified_font_recovery import (
    FontRecoveryManifest,
    ReviewedFontMap,
    ReviewedTextRun,
)

pytestmark = pytest.mark.unit


def _plan():
    return ReviewedPDFRecovery(
        FontRecoveryManifest(
            "a" * 64,
            "synthetic fixture author",
            "fixture source strings",
            (ReviewedFontMap((4, 0), "b" * 64, ((1, "A"), (2, "fi"), (3, " "))),),
            (ReviewedTextRun(0, 1, 5, "A fi"),),
        ),
        ReviewedSemanticManifest(
            "c" * 64,
            "synthetic fixture author",
            "fixture roles",
            "A fi",
            "en",
            (
                ReviewedSemanticNode(
                    "heading", "H1", occurrences=(ReviewedOccurrence(0, 3, "A fi"),)
                ),
            ),
            ("heading",),
        ),
    )


def test_canonical_roundtrip_is_deeply_immutable_and_preserves_ligatures_spaces():
    plan = _plan()
    encoded = serialize_reviewed_pdf_recovery(plan)
    decoded = deserialize_reviewed_pdf_recovery(encoded)
    assert decoded == plan
    assert (
        serialize_reviewed_pdf_recovery(
            deserialize_reviewed_pdf_recovery(json.dumps(json.loads(encoded), indent=2))
        )
        == encoded
    )
    with pytest.raises(FrozenInstanceError):
        decoded.font_manifest = None
    with pytest.raises(FrozenInstanceError):
        decoded.semantic_manifest.nodes[0].role = "P"
    assert isinstance(decoded.font_manifest.fonts[0].mappings, tuple)


def test_receipt_has_hashes_and_factual_provenance_without_content_or_approval():
    plan = _plan()
    receipt = reviewed_pdf_recovery_receipt(plan)
    assert receipt["font_source_sha256"] == plan.font_manifest.source_sha256
    assert receipt["semantic_source_sha256"] == plan.semantic_manifest.source_sha256
    assert receipt["font_review"]["reviewer"] == "synthetic fixture author"
    assert receipt["independent_review_pending"] is True
    assert "A fi" not in json.dumps(receipt)
    assert "approved" not in json.dumps(receipt)
    changed = replace(
        plan,
        font_manifest=replace(
            plan.font_manifest, review_reference="second fixture review"
        ),
    )
    assert (
        reviewed_pdf_recovery_receipt(changed)["bundle_sha256"]
        != receipt["bundle_sha256"]
    )
    assert (
        reviewed_pdf_recovery_receipt(changed)["semantic_manifest_sha256"]
        == receipt["semantic_manifest_sha256"]
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("version",), True),
        (("version",), 1.0),
        (("version",), 2),
        (("font_manifest", "source_sha256"), "A" * 64),
        (("font_manifest", "reviewer"), ""),
        (("font_manifest", "fonts", 0, "objgen", 0), True),
        (("font_manifest", "fonts", 0, "objgen", 1), -1),
        (("font_manifest", "fonts", 0, "mappings", 0, 0), 65536),
        (("font_manifest", "fonts", 0, "mappings", 0, 1), "\ufffd"),
        (("font_manifest", "fonts", 0, "mappings", 0, 1), "\ud800"),
        (("font_manifest", "fonts", 0, "mappings", 0, 1), "\x00"),
        (("font_manifest", "fonts", 0, "mappings", 0, 1), ""),
        (("font_manifest", "fonts", 0, "mappings", 0, 1), "x" * 17),
        (("font_manifest", "runs", 0, "start"), 5),
        (("font_manifest", "runs", 0, "page_index"), 100),
        (("semantic_manifest", "nodes", 0, "role"), "Script"),
        (("semantic_manifest", "nodes", 0, "occurrences", 0, "operator_index"), 100000),
        (("semantic_manifest", "nodes", 0, "occurrences", 0, "text"), None),
        (("semantic_manifest", "nodes", 0, "occurrences", 0, "image_sha256"), "d" * 64),
        (("semantic_manifest", "root_ids"), ["absent"]),
        (("semantic_manifest", "vector_artifacts_reviewed"), "true"),
    ],
)
def test_invalid_fields_fail_closed(path, value):
    data = json.loads(serialize_reviewed_pdf_recovery(_plan()))
    cursor = data
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(PDFRecoveryPlanError, match="^invalid_pdf_recovery_plan$"):
        deserialize_reviewed_pdf_recovery(json.dumps(data))


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "duplicate_font",
        "duplicate_cid",
        "duplicate_run",
        "duplicate_node",
        "duplicate_root",
        "duplicate_artifact",
        "cycle",
    ],
)
def test_ambiguous_or_unknown_data_is_rejected(mutation):
    data = json.loads(serialize_reviewed_pdf_recovery(_plan()))
    font, semantic = data["font_manifest"], data["semantic_manifest"]
    if mutation == "unknown":
        semantic["human_approved"] = True
    elif mutation == "duplicate_font":
        font["fonts"] *= 2
    elif mutation == "duplicate_cid":
        font["fonts"][0]["mappings"].append([1, "Z"])
    elif mutation == "duplicate_run":
        font["runs"] *= 2
    elif mutation == "duplicate_node":
        semantic["nodes"] *= 2
    elif mutation == "duplicate_root":
        semantic["root_ids"] *= 2
    elif mutation == "duplicate_artifact":
        semantic["artifacts"] = semantic["nodes"][0]["occurrences"]
    else:
        semantic["nodes"].append(
            {
                **semantic["nodes"][0],
                "node_id": "cycle",
                "children": ["cycle"],
                "occurrences": [],
            }
        )
    with pytest.raises(PDFRecoveryPlanError):
        deserialize_reviewed_pdf_recovery(json.dumps(data))


@pytest.mark.parametrize(
    "raw",
    [
        '{"version":1,"version":1}',
        '{"font_manifest":{"reviewer":"a","reviewer":"b"}}',
        "[" * 17 + "]" * 17,
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1.1}',
        "null",
        "{}",
        "[]",
        "not json",
    ],
)
def test_json_duplicates_nonfinite_numbers_and_depth_are_rejected(raw):
    with pytest.raises(PDFRecoveryPlanError):
        deserialize_reviewed_pdf_recovery(raw)


def test_typed_serializer_rejects_mutable_payload_and_byte_limits(monkeypatch):
    plan = _plan()
    with pytest.raises(PDFRecoveryPlanError):
        serialize_reviewed_pdf_recovery(
            json.loads(serialize_reviewed_pdf_recovery(plan))
        )
    with pytest.raises(PDFRecoveryPlanError):
        serialize_reviewed_pdf_recovery(
            replace(
                plan,
                font_manifest=replace(
                    plan.font_manifest, fonts=list(plan.font_manifest.fonts)
                ),
            )
        )
    raw = serialize_reviewed_pdf_recovery(plan)
    monkeypatch.setattr(module, "MAX_RECOVERY_JSON_BYTES", len(raw) - 1)
    with pytest.raises(PDFRecoveryPlanError):
        serialize_reviewed_pdf_recovery(plan)
    with pytest.raises(PDFRecoveryPlanError):
        deserialize_reviewed_pdf_recovery(raw)


def test_byte_bounds_include_non_ascii_transport_and_item_budget(monkeypatch):
    raw = serialize_reviewed_pdf_recovery(_plan())
    non_ascii = raw.replace("fixture roles", "é" * 50)
    monkeypatch.setattr(module, "MAX_RECOVERY_JSON_BYTES", len(non_ascii))
    with pytest.raises(PDFRecoveryPlanError):
        deserialize_reviewed_pdf_recovery(non_ascii)
    monkeypatch.setattr(module, "MAX_RECOVERY_JSON_BYTES", 4 * 1024 * 1024)
    monkeypatch.setattr(module, "_MAX_ITEMS", 10)
    with pytest.raises(PDFRecoveryPlanError):
        serialize_reviewed_pdf_recovery(_plan())
    with pytest.raises(PDFRecoveryPlanError):
        deserialize_reviewed_pdf_recovery(raw)
