"""Exact crop-source and MathFixer routing tests for scanned equations."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw

from src.education.math_contracts import SCANNED_EQUATION_REGION_ISSUE_TYPE
from src.education.pdf_checks.equation_region_detector import _pixel_digest
from src.education.remediation.equation_image_source import EquationRegionSource
from src.education.remediation.math_fixer import (
    MathFixer,
    PendingScannedRegionAssociation,
)


def _source_and_metadata():
    image = Image.new("RGB", (100, 80), "white")
    ImageDraw.Draw(image).text((12, 22), "x=2", fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    source = output.getvalue()
    pixel_bbox = (10, 20, 40, 40)
    crop = image.crop(pixel_bbox)
    fields = {
        "source_kind": "page_raster_region",
        "page_number": 1,
        "parent_occurrence_id": "imgocc-v1-" + ("1" * 24),
        "image_xref": 5,
        "image_index": 0,
        "occurrence_ordinal": 0,
        "parent_bbox": [0.0, 0.0, 100.0, 80.0],
        "pixel_bbox": list(pixel_bbox),
        "pdf_bbox": [10.0, 20.0, 40.0, 40.0],
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "crop_pixel_sha256": _pixel_digest(crop),
        "source_width": 100,
        "source_height": 80,
        "detector_version": "raster-equation-region-v1",
        "threshold_version": "grayscale-lt245-v1",
        "ocr_engine_version": "5.5.1",
        "ocr_tessdata_sha256": (
            "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"
        ),
        "ocr_language": "eng",
        "ocr_config": "--oem 3 --psm 6",
        "transform": [100.0, 0.0, 0.0, 80.0, 0.0, 0.0],
    }
    encoded = json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    fields["region_id"] = "eqregion-v1-" + hashlib.sha256(encoded).hexdigest()[:24]
    fields["issue_type"] = SCANNED_EQUATION_REGION_ISSUE_TYPE
    return source, fields


class _WorkingPage:
    rotation = 0

    def get_images(self, full=True):
        assert full is True
        return [(13,)]

    def get_image_info(self, xrefs=True):
        assert xrefs is True
        return [
            {
                "xref": 13,
                "bbox": (0.0, 0.0, 100.0, 80.0),
                "transform": (100.0, 0.0, 0.0, 80.0, 0.0, 0.0),
            }
        ]

    def get_drawings(self):
        return []


class _WorkingDocument:
    def __init__(self, source):
        self.source = source

    def __getitem__(self, index):
        assert index == 0
        return _WorkingPage()

    def extract_image(self, xref):
        assert xref == 13
        return {"image": self.source, "ext": "png"}


def test_region_source_remaps_ocr_xref_but_keeps_original_locator():
    source, metadata = _source_and_metadata()

    validated = EquationRegionSource().extract(_WorkingDocument(source), metadata)

    assert validated.width == 30
    assert validated.height == 20
    assert validated.jpeg_bytes.startswith(b"\xff\xd8")
    assert validated.identity.image_xref == 5
    assert validated.identity.parent_occurrence_id == "imgocc-v1-" + ("1" * 24)
    assert validated.working_occurrence.image_xref == 13
    assert (
        validated.working_occurrence.occurrence_id != metadata["parent_occurrence_id"]
    )


class _Recognizer:
    def __init__(self):
        self.payloads = []

    def recognize(self, payload):
        self.payloads.append(payload)
        return SimpleNamespace(
            classification="printed_equation",
            latex="x^2",
            provider="ollama",
            model="local-vision",
        )


class _Verifier:
    def __init__(self, mathml, source_sha256):
        self.mathml = mathml
        self.source_sha256 = source_sha256

    def verify(self, payload, latex):
        assert payload.width == 30 and payload.height == 20
        assert latex == "x^2"
        return SimpleNamespace(
            passed=True,
            source_sha256=self.source_sha256,
            rendered_sha256="b" * 64,
            mathml_sha256=hashlib.sha256(self.mathml.encode()).hexdigest(),
            renderer_version="renderer-v1",
            comparator_version="compare-v1",
            font_sha256="f" * 64,
            threshold_version="printed-equation-v1",
            ink_iou=1.0,
            pixel_similarity=1.0,
            required_ink_iou=0.9,
            required_pixel_similarity=0.98,
        )


def _region_fixer(source, metadata, recognizer):
    region_source = EquationRegionSource()
    validated = region_source.extract(_WorkingDocument(source), metadata)
    fixer = MathFixer(
        SimpleNamespace(pages=[object()]),
        _WorkingDocument(source),
        struct_tree=SimpleNamespace(),
        alt_text_client=SimpleNamespace(purpose="alt_text"),
        region_source=region_source,
        equation_recognizer=recognizer,
        equation_verifier=SimpleNamespace(),
    )
    mathml = fixer._convert_to_mathml("x^2")
    fixer.equation_verifier = _Verifier(mathml, validated.normalized_sha256)
    return fixer


def test_math_fixer_sends_only_proven_crop_and_stages_distinct_region_request():
    source, metadata = _source_and_metadata()
    recognizer = _Recognizer()
    fixer = _region_fixer(source, metadata, recognizer)

    result = fixer._fix_math_issue(SimpleNamespace(metadata=metadata))

    assert result.error == "scanned_equation_region_association_pending"
    assert result.source_kind == "image_equation"
    assert result.confidence == 0.55
    assert result.needs_review is True
    assert len(recognizer.payloads) == 1
    assert recognizer.payloads[0].width == 30
    assert recognizer.payloads[0].height == 20
    assert isinstance(result.pending_association, PendingScannedRegionAssociation)
    assert result.pending_association.locator.image_xref == 5
    assert result.pending_association.image_xref == 13
    assert result.pending_association.bbox == (10.0, 20.0, 40.0, 40.0)
    assert result.pending_association.parent_bbox == (0.0, 0.0, 100.0, 80.0)


def test_tampered_region_evidence_rejects_before_provider_invocation():
    source, metadata = _source_and_metadata()
    recognizer = _Recognizer()
    fixer = _region_fixer(source, metadata, recognizer)
    tampered = {**metadata, "crop_pixel_sha256": "0" * 64}

    result = fixer._fix_math_issue(SimpleNamespace(metadata=tampered))

    assert result.error == "equation_region_source_rejected"
    assert recognizer.payloads == []
