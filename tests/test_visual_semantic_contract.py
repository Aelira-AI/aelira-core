"""Strict core contracts for typed visual semantics and verification evidence."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

HASHES = tuple(character * 64 for character in "abcdef1234567890")


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _page_locator(**overrides: object) -> dict[str, object]:
    identity: dict[str, object] = {
        "source_kind": "page_raster_region",
        "page_number": 1,
        "parent_occurrence_id": "imgocc-v1-" + ("7" * 24),
        "image_xref": 7,
        "image_index": 0,
        "occurrence_ordinal": 0,
        "parent_bbox": [0.0, 0.0, 612.0, 792.0],
        "pixel_bbox": [100, 200, 500, 320],
        "pdf_bbox": [30.6, 63.36, 153.0, 101.376],
        "source_sha256": HASHES[0],
        "crop_pixel_sha256": HASHES[1],
        "source_width": 2000,
        "source_height": 2500,
        "detector_version": "raster-equation-region-v1",
        "threshold_version": "grayscale-lt245-v1",
        "ocr_engine_version": "5.3.4",
        "ocr_tessdata_sha256": HASHES[2],
        "ocr_language": "eng",
        "ocr_config": "--oem 3 --psm 6",
        "transform": [612.0, 0.0, 0.0, 792.0, 0.0, 0.0],
    }
    identity.update(overrides)
    return {**identity, "region_id": "eqregion-v1-" + _sha256(identity)[:24]}


def _embedded_locator(**overrides: object) -> dict[str, object]:
    identity: dict[str, object] = {
        "source_kind": "embedded_image_occurrence",
        "page_number": 2,
        "image_xref": 11,
        "image_index": 3,
        "occurrence_ordinal": 1,
        "bbox": [12.5, 20.0, 240.0, 100.5],
        "image_stream_sha256": HASHES[3],
    }
    identity.update(overrides)
    occurrence_material = "2|11|3|1|12.500000,20.000000,240.000000,100.500000"
    return {
        **identity,
        "occurrence_id": "imgocc-v1-"
        + hashlib.sha256(occurrence_material.encode("utf-8")).hexdigest()[:24],
    }


def _semantic(**overrides: object) -> dict[str, object]:
    mathml = "<math><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow></math>"
    value: dict[str, object] = {
        "semantic_kind": "mathml_expression_v1",
        "mathml": mathml,
        "alt_text": "x plus one",
        "mathml_sha256": hashlib.sha256(mathml.encode("utf-8")).hexdigest(),
    }
    value.update(overrides)
    return value


def _alt_text_sha256() -> str:
    return hashlib.sha256(str(_semantic()["alt_text"]).encode("utf-8")).hexdigest()


def _roundtrip(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "evidence_kind": "printed_equation_roundtrip_v1",
        "passed": True,
        "source_sha256": HASHES[4],
        "rendered_sha256": HASHES[5],
        "mathml_sha256": _semantic()["mathml_sha256"],
        "renderer_version": "chromium-1",
        "comparator_version": "pixel-v1",
        "font_sha256": HASHES[6],
        "threshold_version": "printed-equation-v1",
        "ink_iou": 0.99,
        "pixel_similarity": 0.995,
        "required_ink_iou": 0.9,
        "required_pixel_similarity": 0.98,
    }
    value.update(overrides)
    return value


def _standalone_saved(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "evidence_kind": "standalone_formula_saved_v1",
        "passed": True,
        "saved_file_sha256": HASHES[7],
        "page_number": 2,
        "image_xref": 11,
        "occurrence_ordinal": 1,
        "struct_parent": 4,
        "mcid": 8,
        "mathml_sha256": _semantic()["mathml_sha256"],
        "alt_text_sha256": _alt_text_sha256(),
        "image_stream_sha256": HASHES[3],
    }
    value.update(overrides)
    return value


def _scanned_saved(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "evidence_kind": "scanned_region_formula_saved_v1",
        "passed": True,
        "saved_file_sha256": HASHES[7],
        "page_number": 1,
        "image_xref": 7,
        "resource_name": "Im7",
        "struct_parent": 4,
        "mcid": 8,
        "mathml_sha256": _semantic()["mathml_sha256"],
        "alt_text_sha256": _alt_text_sha256(),
        "image_stream_sha256": HASHES[0],
        "formula_bbox": [30.6, 690.624, 153.0, 728.64],
        "render_signatures": [[100, 200, 500, 320, 7, HASHES[8]]],
        "ocr_resource_name": "OCR1",
        "ocr_struct_parent": 5,
        "ocr_group_owners": [["page", 0]],
        "ocr_before_mcids": [1, 2],
        "ocr_after_mcids": [9, 10],
        "ocr_payload_sha256": HASHES[9],
        "ocr_font_sha256": HASHES[10],
        "page_text_sha256": HASHES[11],
    }
    value.update(overrides)
    return value


def _contract(
    locator: dict[str, object],
    saved: dict[str, object],
    *,
    semantic: dict[str, object] | None = None,
    evidence: list[dict[str, object]] | None = None,
    contract_kind: str = "printed_equation",
) -> dict[str, object]:
    output = semantic or _semantic()
    proofs = evidence or [_roundtrip(), saved]
    specialist = {
        "contract_kind": contract_kind,
        "locator": locator,
        "semantic_output": output,
        "normalized_source_sha256": _roundtrip()["source_sha256"],
    }
    value: dict[str, object] = {
        **specialist,
        "verification_evidence": proofs,
        "specialist_sha256": _sha256(specialist),
    }
    value["contract_sha256"] = _sha256(value)
    return value


def test_locator_union_preserves_existing_region_model_and_exact_embedded_identity():
    from src.education.equation_region_contract import PageRasterRegionLocator
    from src.education.visual_semantic_contract import (
        EmbeddedImageOccurrenceLocator,
        VisualLocatorAdapter,
    )

    region = VisualLocatorAdapter.validate_python(_page_locator())
    embedded = VisualLocatorAdapter.validate_python(_embedded_locator())

    assert isinstance(region, PageRasterRegionLocator)
    assert region.model_dump(mode="json") == _page_locator()
    assert region.model_config["frozen"] is True
    assert isinstance(embedded, EmbeddedImageOccurrenceLocator)
    assert embedded.model_config["frozen"] is True

    with pytest.raises(ValidationError):
        EmbeddedImageOccurrenceLocator.model_validate(
            {**_embedded_locator(), "occurrence_id": "imgocc-v1-" + "0" * 24}
        )
    with pytest.raises(ValidationError):
        VisualLocatorAdapter.validate_python(
            {**_embedded_locator(), "source_kind": "reserved_future_locator"}
        )
    with pytest.raises(ValidationError):
        VisualLocatorAdapter.validate_python(
            {**_embedded_locator(), "payload": "secret"}
        )


def test_semantic_and_evidence_unions_are_exact_frozen_and_fail_closed():
    from src.education.visual_semantic_contract import (
        SemanticOutputAdapter,
        VerificationEvidenceAdapter,
    )

    semantic = SemanticOutputAdapter.validate_python(_semantic())
    evidence = VerificationEvidenceAdapter.validate_python(_roundtrip())

    assert semantic.model_config["extra"] == "forbid"
    assert semantic.model_config["frozen"] is True
    assert evidence.model_config["frozen"] is True
    with pytest.raises(ValidationError):
        semantic.mathml = "<math><mi>y</mi></math>"

    invalid_values = (
        {**_semantic(), "semantic_kind": "latex_expression_v2"},
        {**_semantic(), "mathml": "<math><script>run()</script></math>"},
        {**_semantic(), "mathml_sha256": HASHES[0]},
        {**_semantic(), "alt_text": "unsafe\ntext"},
        {**_roundtrip(), "evidence_kind": "reserved_verifier_v2"},
        {**_roundtrip(), "renderer_version": "chrome\x00secret"},
        {**_standalone_saved(), "provider_payload": {"secret": True}},
        {**_scanned_saved(), "render_signatures": [[0, 1, 1, 1, 1, HASHES[8]]]},
    )
    for value in invalid_values:
        adapter = (
            SemanticOutputAdapter
            if "semantic_kind" in value
            else VerificationEvidenceAdapter
        )
        with pytest.raises(ValidationError):
            adapter.validate_python(value)


def test_semantic_and_contract_adapters_expose_exact_discriminators():
    from src.education.visual_semantic_contract import (
        SemanticOutputAdapter,
        VisualSemanticContractAdapter,
    )

    assert SemanticOutputAdapter.json_schema()["discriminator"] == {
        "mapping": {
            "commutative_diagram_semantic_v1": "#/$defs/CommutativeDiagramSemanticV1",
            "mathml_expression_v1": "#/$defs/MathMLExpressionV1",
        },
        "propertyName": "semantic_kind",
    }
    assert VisualSemanticContractAdapter.json_schema()["discriminator"] == {
        "mapping": {
            "commutative_diagram": "#/$defs/CommutativeDiagramPdfContract",
            "handwritten_equation": "#/$defs/HandwrittenEquationContract",
            "printed_equation": "#/$defs/PrintedEquationContract",
        },
        "propertyName": "contract_kind",
    }
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python(
            _contract(
                _embedded_locator(),
                _standalone_saved(),
                contract_kind="reserved_visual_kind",
            )
        )


def test_canonical_json_ignores_mapping_order_but_preserves_list_order():
    from src.education.visual_semantic_contract import (
        canonical_json_bytes,
        canonical_sha256,
    )

    left = {"z": ["first", "second"], "a": {"right": 2, "left": 1}}
    reordered = {"a": {"left": 1, "right": 2}, "z": ["first", "second"]}
    list_reordered = {"a": {"left": 1, "right": 2}, "z": ["second", "first"]}

    assert canonical_json_bytes(left) == canonical_json_bytes(reordered)
    assert canonical_sha256(left) == canonical_sha256(reordered)
    assert canonical_sha256(left) != canonical_sha256(list_reordered)

    for unsafe in (
        {"value": float("nan")},
        {"value": b"active-bytes"},
        {"value": "control\x00text"},
        {1: "non-string-key"},
    ):
        with pytest.raises((TypeError, ValueError)):
            canonical_json_bytes(unsafe)


@pytest.mark.parametrize(
    ("locator", "saved"),
    [
        (_embedded_locator(), _standalone_saved()),
        (_page_locator(), _scanned_saved()),
    ],
)
def test_printed_equation_contract_accepts_each_exact_locator_saved_pair(
    locator: dict[str, object], saved: dict[str, object]
):
    from src.education.visual_semantic_contract import PrintedEquationContract

    raw = _contract(locator, saved)
    contract = PrintedEquationContract.model_validate(raw)

    assert contract.model_dump(mode="json") == raw
    assert contract.model_config["extra"] == "forbid"
    assert contract.model_config["frozen"] is True


def test_contract_rejects_forged_digests_cross_variants_and_reserved_kinds():
    from src.education.visual_semantic_contract import PrintedEquationContract

    embedded = _contract(_embedded_locator(), _standalone_saved())
    invalid = (
        {**embedded, "specialist_sha256": HASHES[0]},
        {**embedded, "contract_sha256": HASHES[0]},
        _contract(_embedded_locator(), _scanned_saved()),
        _contract(_page_locator(), _standalone_saved()),
        _contract(
            _embedded_locator(),
            _standalone_saved(mathml_sha256=HASHES[0]),
        ),
        _contract(
            _embedded_locator(),
            _standalone_saved(alt_text_sha256=HASHES[0]),
        ),
        _contract(
            _embedded_locator(),
            _standalone_saved(),
            evidence=[_roundtrip(), _roundtrip()],
        ),
        _contract(
            _embedded_locator(),
            _standalone_saved(),
            contract_kind="reserved_visual_kind",
        ),
    )
    for value in invalid:
        with pytest.raises(ValidationError):
            PrintedEquationContract.model_validate(value)


def test_contract_digest_changes_when_ordered_evidence_changes():
    from src.education.visual_semantic_contract import PrintedEquationContract

    normal = _contract(_embedded_locator(), _standalone_saved())
    reversed_evidence = _contract(
        _embedded_locator(),
        _standalone_saved(),
        evidence=[_standalone_saved(), _roundtrip()],
    )

    assert normal["contract_sha256"] != reversed_evidence["contract_sha256"]
    assert PrintedEquationContract.model_validate(normal)
    assert PrintedEquationContract.model_validate(reversed_evidence)


@pytest.mark.parametrize(
    "roundtrip",
    [
        _roundtrip(passed=False),
        _roundtrip(ink_iou=0.89),
        _roundtrip(pixel_similarity=0.97),
    ],
)
def test_complete_contract_rejects_failed_or_below_threshold_roundtrip(
    roundtrip: dict[str, object],
):
    from src.education.visual_semantic_contract import PrintedEquationContract

    raw = _contract(
        _embedded_locator(),
        _standalone_saved(),
        evidence=[roundtrip, _standalone_saved()],
    )
    with pytest.raises(ValidationError):
        PrintedEquationContract.model_validate(raw)


def test_complete_contract_has_a_deeply_frozen_page_region_locator():
    from src.education.visual_semantic_contract import PrintedEquationContract

    contract = PrintedEquationContract.model_validate(
        _contract(_page_locator(), _scanned_saved())
    )
    digest = contract.contract_sha256

    with pytest.raises(ValidationError):
        contract.locator.page_number = 2
    assert contract.contract_sha256 == digest


@pytest.mark.parametrize(
    "mathml",
    [
        "<math><?target payload?><mi>x</mi></math>",
        "<math><foreign>x</foreign></math>",
        '<math xmlns:e="urn:evil"><e:mi>x</e:mi></math>',
        '<math><mi onclick="run()">x</mi></math>',
        '<math><mi style="background:url(data:text/plain,x)">x</mi></math>',
        '<math><mi href="https://example.invalid/x">x</mi></math>',
        "<math><mtext>x</mtext></math>",
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>',
    ],
)
def test_semantic_contract_reuses_exact_canonical_mathml_allowlist(mathml: str):
    from src.education.visual_semantic_contract import SemanticOutputAdapter

    raw = _semantic(
        mathml=mathml,
        mathml_sha256=hashlib.sha256(mathml.encode("utf-8")).hexdigest(),
    )
    with pytest.raises(ValidationError):
        SemanticOutputAdapter.validate_python(raw)


def test_normalized_source_digest_is_specialist_bound_and_matches_roundtrip():
    from src.education.visual_semantic_contract import PrintedEquationContract

    raw = _contract(_embedded_locator(), _standalone_saved())
    changed = {
        **raw,
        "normalized_source_sha256": HASHES[0],
    }
    specialist = {
        "contract_kind": changed["contract_kind"],
        "locator": changed["locator"],
        "semantic_output": changed["semantic_output"],
        "normalized_source_sha256": changed["normalized_source_sha256"],
    }
    changed["specialist_sha256"] = _sha256(specialist)
    changed["contract_sha256"] = _sha256(
        {key: value for key, value in changed.items() if key != "contract_sha256"}
    )

    assert raw["specialist_sha256"] != changed["specialist_sha256"]
    with pytest.raises(ValidationError):
        PrintedEquationContract.model_validate(changed)


@pytest.mark.parametrize(
    ("locator", "saved"),
    [
        (_embedded_locator(), _standalone_saved(alt_text_sha256=HASHES[0])),
        (_page_locator(), _scanned_saved(alt_text_sha256=HASHES[0])),
    ],
)
def test_saved_evidence_alt_text_digest_is_semantically_bound(
    locator: dict[str, object], saved: dict[str, object]
):
    from src.education.visual_semantic_contract import PrintedEquationContract

    with pytest.raises(ValidationError):
        PrintedEquationContract.model_validate(_contract(locator, saved))


def test_embedded_locator_id_matches_existing_occurrence_algorithm_and_is_frozen():
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.visual_semantic_contract import VisualLocatorAdapter

    class Page:
        @staticmethod
        def get_images(*, full: bool):
            assert full is True
            return [(8,), (9,), (11,)]

        @staticmethod
        def get_image_info(*, xrefs: bool):
            assert xrefs is True
            return [
                {"xref": 8, "bbox": (1.0, 1.0, 2.0, 2.0)},
                {"xref": 11, "bbox": (3.0, 3.0, 4.0, 4.0)},
                {"xref": 9, "bbox": (5.0, 5.0, 6.0, 6.0)},
                {"xref": 11, "bbox": (12.5, 20.0, 240.0, 100.5)},
            ]

    existing = _displayed_image_occurrences(Page(), 2)[3]
    raw = _embedded_locator(occurrence_id=existing["occurrence_id"])
    locator = VisualLocatorAdapter.validate_python(raw)

    assert locator.occurrence_id == existing["occurrence_id"]
    with pytest.raises(ValidationError):
        locator.page_number = 3


def test_scanned_saved_output_xref_may_remap_when_source_bytes_still_match():
    from src.education.visual_semantic_contract import PrintedEquationContract

    contract = PrintedEquationContract.model_validate(
        _contract(_page_locator(), _scanned_saved(image_xref=91))
    )

    saved = next(
        evidence
        for evidence in contract.verification_evidence
        if evidence.evidence_kind == "scanned_region_formula_saved_v1"
    )
    assert saved.image_xref == 91
    assert saved.image_stream_sha256 == contract.locator.source_sha256


def test_scanned_saved_output_rejects_mismatched_source_stream_digest():
    from src.education.visual_semantic_contract import PrintedEquationContract

    with pytest.raises(ValidationError):
        PrintedEquationContract.model_validate(
            _contract(
                _page_locator(),
                _scanned_saved(image_xref=91, image_stream_sha256=HASHES[1]),
            )
        )


def test_scanned_saved_formula_bbox_uses_bottom_left_pdf_coordinates():
    from src.education.visual_semantic_contract import PrintedEquationContract

    contract = PrintedEquationContract.model_validate(
        _contract(_page_locator(), _scanned_saved())
    )
    saved = next(
        evidence
        for evidence in contract.verification_evidence
        if evidence.evidence_kind == "scanned_region_formula_saved_v1"
    )

    assert saved.formula_bbox == pytest.approx((30.6, 690.624, 153.0, 728.64))


def test_scanned_saved_formula_bbox_rejects_top_left_or_forged_coordinates():
    from src.education.visual_semantic_contract import PrintedEquationContract

    with pytest.raises(ValidationError):
        PrintedEquationContract.model_validate(
            _contract(
                _page_locator(),
                _scanned_saved(formula_bbox=[30.6, 63.36, 153.0, 101.376]),
            )
        )
