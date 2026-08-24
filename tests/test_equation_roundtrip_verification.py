"""Fail-closed tests for image-equation visual verification.

The comparator is a narrow visual filter. These tests deliberately do not claim
that pixel similarity proves mathematical equivalence.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src.education.remediation.equation_image_source import (
    EquationImageIdentity,
    ValidatedEquationImage,
)


def _png(draw) -> bytes:
    image = Image.new("RGB", (240, 90), "white")
    draw(ImageDraw.Draw(image))
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _validated(payload: bytes) -> ValidatedEquationImage:
    with Image.open(BytesIO(payload)) as image:
        jpeg = BytesIO()
        image.convert("RGB").save(jpeg, "JPEG", quality=95, subsampling=0)
        encoded = jpeg.getvalue()
        width, height = image.size
    digest = hashlib.sha256(encoded).hexdigest()
    return ValidatedEquationImage(
        jpeg_bytes=encoded,
        mime_type="image/jpeg",
        source_sha256=digest,
        normalized_sha256=digest,
        width=width,
        height=height,
        identity=EquationImageIdentity(1, 7, 0, 0, (1, 2, 3, 4), "occ-1"),
    )


def test_identical_round_trip_returns_only_bounded_typed_evidence():
    from src.education.remediation.equation_verifier import EquationVerifier

    source = _png(lambda pen: pen.line((20, 45, 220, 45), fill="black", width=5))
    verifier = EquationVerifier(
        converter=lambda latex: '<math><mi>x</mi><mo>=</mo><mn>1</mn></math>',
        renderer=lambda mathml: source,
    )

    evidence = verifier.verify(_validated(source), "x=1")

    assert evidence.passed is True
    assert evidence.ink_iou == pytest.approx(1.0)
    assert evidence.pixel_similarity >= evidence.required_pixel_similarity
    serialized = dataclasses.asdict(evidence)
    assert set(serialized) == {
        "passed",
        "source_sha256",
        "rendered_sha256",
        "mathml_sha256",
        "renderer_version",
        "comparator_version",
        "font_sha256",
        "threshold_version",
        "ink_iou",
        "pixel_similarity",
        "required_ink_iou",
        "required_pixel_similarity",
    }
    assert "x=1" not in repr(evidence)
    assert len(json.dumps(serialized)) < 2048


def test_near_miss_operator_is_rejected_by_comparison():
    from src.education.remediation.equation_verifier import EquationVerifier

    source = _png(lambda pen: pen.line((25, 35, 215, 35), fill="black", width=5))
    changed = _png(
        lambda pen: (
            pen.line((25, 25, 215, 65), fill="black", width=5),
            pen.line((25, 65, 215, 25), fill="black", width=5),
        )
    )
    verifier = EquationVerifier(
        converter=lambda latex: '<math><mi>x</mi><mo>+</mo><mn>1</mn></math>',
        renderer=lambda mathml: changed,
    )

    evidence = verifier.verify(_validated(source), "x+1")

    assert evidence.passed is False
    assert evidence.ink_iou < evidence.required_ink_iou


@pytest.mark.parametrize(
    "mathml",
    [
        "",
        "<math></math>",
        "<math><mtext>unparsed latex</mtext></math>",
        "<html><mi>x</mi></html>",
        "<math><script>alert(1)</script><mi>x</mi></math>",
        "<math><annotation src='https://example.invalid'>x</annotation></math>",
        '<math xmlns="urn:evil"><mi>x</mi></math>',
        '<math xmlns:e="urn:evil"><e:mi>x</e:mi></math>',
    ],
)
def test_invalid_unbounded_or_fallback_mathml_fails_closed(mathml):
    from src.education.remediation.equation_verifier import EquationVerificationRejected
    from src.education.remediation.equation_verifier import EquationVerifier

    verifier = EquationVerifier(converter=lambda latex: mathml, renderer=lambda value: b"png")

    with pytest.raises(EquationVerificationRejected):
        verifier.verify(_validated(_png(lambda pen: pen.point((5, 5), fill="black"))), "x")


@pytest.mark.parametrize(
    "attribute",
    [
        'style="background:url(https://example.invalid/x)"',
        'onclick="alert(1)"',
        'href="https://example.invalid/x"',
        'xmlns:evil="urn:evil" evil:payload="x"',
    ],
)
def test_mathml_active_or_foreign_attributes_fail_closed(attribute):
    from src.education.remediation.equation_verifier import EquationVerificationRejected
    from src.education.remediation.equation_verifier import EquationVerifier

    mathml = f"<math><mi {attribute}>x</mi></math>"
    verifier = EquationVerifier(converter=lambda latex: mathml, renderer=lambda value: b"png")
    with pytest.raises(EquationVerificationRejected, match="invalid_mathml"):
        verifier.verify(_validated(_png(lambda pen: pen.point((5, 5), fill="black"))), "x")


@pytest.mark.parametrize(
    "mathml",
    [
        '<math xmlns="urn:evil"><mi>x</mi></math>',
        '<math xmlns="http://www.w3.org/1998/Math/MathML" '
        'xmlns:e="urn:evil"><e:mi>x</e:mi></math>',
        '<math xmlns="http://www.w3.org/1998/Math/MathML" '
        'xmlns:e="http://www.w3.org/1998/Math/MathML}evil"><e:mi>x</e:mi></math>',
    ],
)
def test_foreign_element_namespaces_reject_before_render(mathml):
    from src.education.remediation.equation_verifier import EquationVerificationRejected
    from src.education.remediation.equation_verifier import EquationVerifier

    verifier = EquationVerifier(
        converter=lambda latex: mathml,
        renderer=lambda value: (_ for _ in ()).throw(
            AssertionError("foreign MathML must not reach renderer")
        ),
    )
    with pytest.raises(EquationVerificationRejected, match="invalid_mathml"):
        verifier.verify(_validated(_png(lambda pen: pen.point((5, 5), fill="black"))), "x")


def test_converter_renderer_blank_and_comparator_failures_close():
    from src.education.remediation.equation_verifier import EquationVerificationRejected
    from src.education.remediation.equation_verifier import EquationVerifier

    image = _validated(_png(lambda pen: pen.rectangle((20, 20, 80, 60), fill="black")))
    mathml = '<math><mi>x</mi></math>'

    for verifier in (
        EquationVerifier(converter=lambda latex: (_ for _ in ()).throw(RuntimeError())),
        EquationVerifier(converter=lambda latex: mathml, renderer=lambda value: (_ for _ in ()).throw(TimeoutError())),
        EquationVerifier(converter=lambda latex: mathml, renderer=lambda value: _png(lambda pen: None)),
        EquationVerifier(
            converter=lambda latex: mathml,
            renderer=lambda value: image.jpeg_bytes,
            comparator=lambda left, right: (_ for _ in ()).throw(RuntimeError()),
        ),
    ):
        with pytest.raises(EquationVerificationRejected):
            verifier.verify(image, "x")


def test_sabotage_cannot_bypass_a_failed_comparator():
    from src.education.remediation.equation_verifier import ComparisonMetrics, EquationVerifier

    payload = _png(lambda pen: pen.ellipse((20, 20, 80, 70), fill="black"))
    verifier = EquationVerifier(
        converter=lambda latex: '<math><mi>x</mi></math>',
        renderer=lambda value: payload,
        comparator=lambda left, right: ComparisonMetrics(ink_iou=0.0, pixel_similarity=0.0),
    )

    evidence = verifier.verify(_validated(payload), "x")

    assert evidence.passed is False


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -0.1, 1.1])
def test_nonfinite_or_out_of_range_metrics_fail_closed(value):
    from src.education.remediation.equation_verifier import (
        ComparisonMetrics,
        EquationVerificationRejected,
        EquationVerifier,
    )

    payload = _png(lambda pen: pen.ellipse((20, 20, 80, 70), fill="black"))
    verifier = EquationVerifier(
        converter=lambda latex: '<math><mi>x</mi></math>',
        renderer=lambda value: payload,
        comparator=lambda left, right: ComparisonMetrics(
            ink_iou=value, pixel_similarity=1.0
        ),
    )
    with pytest.raises(EquationVerificationRejected, match="comparison_failed"):
        verifier.verify(_validated(payload), "x")


def test_committed_calibration_manifest_has_separated_supported_corpus():
    from src.education.remediation.equation_verifier import VerifierConfig

    path = Path("tests/fixtures/pdfs/image_equations/manifest.json")
    manifest = json.loads(path.read_text())
    config = VerifierConfig()

    assert manifest["scope"] == "printed_standalone_single_equation"
    assert manifest["threshold_version"] == config.threshold_version
    assert manifest["font_sha256"] == config.font_sha256
    assert len(manifest["positive_pairs"]) >= 5
    assert len(manifest["near_miss_pairs"]) >= 6
    positive_floor = min(item["ink_iou"] for item in manifest["positive_pairs"])
    negative_ceiling = max(item["ink_iou"] for item in manifest["near_miss_pairs"])
    assert positive_floor >= config.required_ink_iou
    assert negative_ceiling < config.required_ink_iou
    assert positive_floor - negative_ceiling >= manifest["minimum_documented_margin"]
    assert manifest["claim"] == "visual_filter_only_not_mathematical_equivalence"


def test_pinned_font_and_license_match_manifest_hashes():
    from src.education.remediation.equation_verifier import FONT_PATH, LICENSE_PATH, VerifierConfig

    config = VerifierConfig()
    font = FONT_PATH.read_bytes()
    license_text = LICENSE_PATH.read_text()

    assert hashlib.sha256(font).hexdigest() == config.font_sha256
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "STIX" in license_text


def test_real_renderer_is_stable_and_blocks_network(monkeypatch):
    from src.education.remediation.equation_verifier import OfflineMathMLRenderer

    renderer = OfflineMathMLRenderer()
    mathml = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mfrac><mi>x</mi><mn>2</mn></mfrac></math>'
    try:
        first = renderer.render(mathml)
        second = renderer.render(mathml)
    except RuntimeError as exc:
        pytest.skip(f"pinned Chromium unavailable in this developer environment: {exc}")

    assert first == second
    assert first.startswith(b"\x89PNG\r\n\x1a\n")
    assert renderer.network_requests == 0


def test_pinned_renderer_calibration_separates_supported_math_near_misses():
    from latex2mathml.converter import convert

    from src.education.remediation.equation_verifier import (
        EquationVerifier,
        OfflineMathMLRenderer,
    )

    manifest = json.loads(
        Path("tests/fixtures/pdfs/image_equations/manifest.json").read_text()
    )
    renderer = OfflineMathMLRenderer()
    verifier = EquationVerifier(renderer=renderer.render)
    executed_positive = []
    executed_negative = []
    for item in manifest["positive_pairs"]:
        source = _validated(renderer.render(convert(item["latex"])))
        evidence = verifier.verify(source, item["latex"])
        assert evidence.passed is True
        assert evidence.ink_iou == pytest.approx(item["ink_iou"], abs=0.005)
        assert evidence.pixel_similarity == pytest.approx(
            item["pixel_similarity"], abs=0.005
        )
        executed_positive.append(evidence.ink_iou)
    for item in manifest["near_miss_pairs"]:
        source = _validated(renderer.render(convert(item["source_latex"])))
        evidence = verifier.verify(source, item["changed_latex"])
        assert evidence.passed is False
        assert evidence.ink_iou == pytest.approx(item["ink_iou"], abs=0.005)
        assert evidence.pixel_similarity == pytest.approx(
            item["pixel_similarity"], abs=0.005
        )
        executed_negative.append(evidence.ink_iou)
    assert min(executed_positive) == pytest.approx(0.991489, abs=0.005)
    assert max(executed_negative) == pytest.approx(0.884669, abs=0.005)
    assert verifier.config.required_ink_iou == 0.90
    assert min(executed_positive) - max(executed_negative) >= 0.10


def _hanging_renderer_worker(mathml, font_data, connection, max_width, max_height):
    time.sleep(10)


def test_renderer_has_killable_end_to_end_deadline():
    from src.education.remediation.equation_verifier import OfflineMathMLRenderer

    renderer = OfflineMathMLRenderer(
        timeout_seconds=0.1, worker_target=_hanging_renderer_worker
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="renderer_timeout"):
        renderer.render("<math><mi>x</mi></math>")
    assert time.monotonic() - started < 2


def test_renderer_rejects_oversized_element_before_screenshot():
    from src.education.remediation.equation_verifier import OfflineMathMLRenderer

    renderer = OfflineMathMLRenderer(max_width=100)
    with pytest.raises(RuntimeError, match="renderer_failed"):
        renderer.render('<math><mspace width="1000px"/></math>')
