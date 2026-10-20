"""Fail-closed tests for image-equation visual verification.

The comparator is a narrow visual filter. These tests deliberately do not claim
that pixel similarity proves mathematical equivalence.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
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
        converter=lambda latex: "<math><mi>x</mi><mo>=</mo><mn>1</mn></math>",
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
        converter=lambda latex: "<math><mi>x</mi><mo>+</mo><mn>1</mn></math>",
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

    verifier = EquationVerifier(
        converter=lambda latex: mathml, renderer=lambda value: b"png"
    )

    with pytest.raises(EquationVerificationRejected):
        verifier.verify(
            _validated(_png(lambda pen: pen.point((5, 5), fill="black"))), "x"
        )


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
    verifier = EquationVerifier(
        converter=lambda latex: mathml, renderer=lambda value: b"png"
    )
    with pytest.raises(EquationVerificationRejected, match="invalid_mathml"):
        verifier.verify(
            _validated(_png(lambda pen: pen.point((5, 5), fill="black"))), "x"
        )


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
        verifier.verify(
            _validated(_png(lambda pen: pen.point((5, 5), fill="black"))), "x"
        )


@pytest.mark.parametrize(
    "mathml",
    [
        "<math><?x <style>body{display:none}</style> ?><mi>x</mi></math>",
        '<math><!-- <img src="data:image/png;base64,AAAA"> --><mi>x</mi></math>',
        '<!DOCTYPE math [<!ENTITY injected "x">]><math><mi>&injected;</mi></math>',
    ],
)
def test_non_element_xml_constructs_reject_before_render(mathml):
    from src.education.remediation.equation_verifier import EquationVerificationRejected
    from src.education.remediation.equation_verifier import EquationVerifier

    verifier = EquationVerifier(
        converter=lambda latex: mathml,
        renderer=lambda value: (_ for _ in ()).throw(
            AssertionError("non-element XML must not reach renderer")
        ),
    )
    with pytest.raises(EquationVerificationRejected, match="invalid_mathml"):
        verifier.verify(
            _validated(_png(lambda pen: pen.point((5, 5), fill="black"))), "x"
        )


def test_renderer_receives_only_canonical_allowlisted_tree():
    from src.education.remediation.equation_verifier import EquationVerifier

    payload = _png(lambda pen: pen.line((20, 45, 220, 45), fill="black", width=5))
    rendered_inputs = []
    verifier = EquationVerifier(
        converter=lambda latex: (
            '<math xmlns="http://www.w3.org/1998/Math/MathML">'
            '<mi mathvariant="italic">x</mi></math>'
        ),
        renderer=lambda value: rendered_inputs.append(value) or payload,
    )

    verifier.verify(_validated(payload), "x")

    assert rendered_inputs == ['<math><mi mathvariant="italic">x</mi></math>']


def test_converter_renderer_blank_and_comparator_failures_close():
    from src.education.remediation.equation_verifier import EquationVerificationRejected
    from src.education.remediation.equation_verifier import EquationVerifier

    image = _validated(_png(lambda pen: pen.rectangle((20, 20, 80, 60), fill="black")))
    mathml = "<math><mi>x</mi></math>"

    for verifier in (
        EquationVerifier(converter=lambda latex: (_ for _ in ()).throw(RuntimeError())),
        EquationVerifier(
            converter=lambda latex: mathml,
            renderer=lambda value: (_ for _ in ()).throw(TimeoutError()),
        ),
        EquationVerifier(
            converter=lambda latex: mathml,
            renderer=lambda value: _png(lambda pen: None),
        ),
        EquationVerifier(
            converter=lambda latex: mathml,
            renderer=lambda value: image.jpeg_bytes,
            comparator=lambda left, right: (_ for _ in ()).throw(RuntimeError()),
        ),
    ):
        with pytest.raises(EquationVerificationRejected):
            verifier.verify(image, "x")


def test_sabotage_cannot_bypass_a_failed_comparator():
    from src.education.remediation.equation_verifier import (
        ComparisonMetrics,
        EquationVerifier,
    )

    payload = _png(lambda pen: pen.ellipse((20, 20, 80, 70), fill="black"))
    verifier = EquationVerifier(
        converter=lambda latex: "<math><mi>x</mi></math>",
        renderer=lambda value: payload,
        comparator=lambda left, right: ComparisonMetrics(
            ink_iou=0.0, pixel_similarity=0.0
        ),
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
        converter=lambda latex: "<math><mi>x</mi></math>",
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
    from src.education.remediation.equation_verifier import (
        FONT_PATH,
        LICENSE_PATH,
        VerifierConfig,
    )

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
        assert 0.0 <= evidence.ink_iou <= 1.0
        assert 0.0 <= evidence.pixel_similarity <= 1.0
        executed_positive.append(evidence.ink_iou)
    for item in manifest["near_miss_pairs"]:
        source = _validated(renderer.render(convert(item["source_latex"])))
        evidence = verifier.verify(source, item["changed_latex"])
        assert evidence.passed is False
        assert 0.0 <= evidence.ink_iou <= 1.0
        assert 0.0 <= evidence.pixel_similarity <= 1.0
        executed_negative.append(evidence.ink_iou)
    assert verifier.config.required_ink_iou == 0.90
    assert min(executed_positive) >= verifier.config.required_ink_iou
    assert max(executed_negative) < verifier.config.required_ink_iou
    assert (
        min(executed_positive) - max(executed_negative)
        >= manifest["minimum_documented_margin"]
    )


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


def _worker_that_crashes(mathml, font_data, connection, max_width, max_height):
    os._exit(3)


def _worker_that_reports_an_error(mathml, font_data, connection, max_width, max_height):
    connection.send(("error", b"", 0))
    connection.close()


def _worker_with_an_unknown_status(
    mathml, font_data, connection, max_width, max_height
):
    connection.send(("surprise", b"", 0))
    connection.close()


def _worker_that_reports_the_size_bound(
    mathml, font_data, connection, max_width, max_height
):
    connection.send(("dimension_limit", b"", 0))
    connection.close()


def _worker_that_reports_the_size_bound_after_a_network_attempt(
    mathml, font_data, connection, max_width, max_height
):
    connection.send(("dimension_limit", b"", 1))
    connection.close()


@pytest.mark.parametrize(
    "worker",
    [
        _worker_that_crashes,
        _worker_that_reports_an_error,
        _worker_with_an_unknown_status,
    ],
)
def test_a_failing_worker_still_reports_renderer_failed(worker):
    from src.education.remediation.equation_verifier import (
        EquationVerificationRejected,
        EquationVerifier,
        OfflineMathMLRenderer,
        RendererDimensionLimit,
    )

    renderer = OfflineMathMLRenderer(worker_target=worker)
    with pytest.raises(RuntimeError, match="^renderer_failed$") as direct:
        renderer.render("<math><mi>x</mi></math>")
    assert not isinstance(direct.value, RendererDimensionLimit)

    verifier = EquationVerifier(
        converter=lambda latex: "<math><mi>x</mi></math>", renderer=renderer.render
    )
    image = _validated(_png(lambda pen: pen.rectangle((20, 20, 80, 60), fill="black")))
    with pytest.raises(EquationVerificationRejected, match="^renderer_failed$"):
        verifier.verify(image, "x")


def test_the_workers_size_bound_status_reaches_the_verifier_as_its_own_reason():
    from src.education.remediation.equation_verifier import (
        EquationVerificationRejected,
        EquationVerifier,
        OfflineMathMLRenderer,
        RendererDimensionLimit,
    )

    renderer = OfflineMathMLRenderer(worker_target=_worker_that_reports_the_size_bound)
    with pytest.raises(RendererDimensionLimit, match="^renderer_dimension_limit$"):
        renderer.render("<math><mi>x</mi></math>")

    verifier = EquationVerifier(
        converter=lambda latex: "<math><mi>x</mi></math>", renderer=renderer.render
    )
    image = _validated(_png(lambda pen: pen.rectangle((20, 20, 80, 60), fill="black")))
    with pytest.raises(
        EquationVerificationRejected, match="^renderer_dimension_limit$"
    ):
        verifier.verify(image, "x")


def test_a_network_attempt_is_never_reported_as_an_oversized_formula():
    from src.education.remediation.equation_verifier import (
        EquationVerificationRejected,
        EquationVerifier,
        OfflineMathMLRenderer,
    )

    renderer = OfflineMathMLRenderer(
        worker_target=_worker_that_reports_the_size_bound_after_a_network_attempt
    )
    with pytest.raises(RuntimeError, match="^renderer_network_attempt$"):
        renderer.render("<math><mi>x</mi></math>")

    verifier = EquationVerifier(
        converter=lambda latex: "<math><mi>x</mi></math>", renderer=renderer.render
    )
    image = _validated(_png(lambda pen: pen.rectangle((20, 20, 80, 60), fill="black")))
    with pytest.raises(EquationVerificationRejected, match="^renderer_failed$"):
        verifier.verify(image, "x")


@pytest.fixture(scope="module")
def chromium_ready():
    """Drive the pinned renderer to a success once before reading its failures."""
    from src.education.remediation.equation_verifier import OfflineMathMLRenderer

    try:
        OfflineMathMLRenderer().render("<math><mi>x</mi></math>")
    except RuntimeError as exc:
        pytest.skip(f"pinned Chromium unavailable in this developer environment: {exc}")


@pytest.mark.parametrize(
    "latex",
    [
        r"x^{2}",
        r"\frac{1}{2}",
        r"\sqrt{x}",
        r"x_{i}^{2}",
        r"{x}+1",
        r"{{x}}",
        r"{f}^{2}",
        r"e^{-x}",
        r"\left(x\right)^{2}",
        r"\binom{n}{k}",
        r"\dbinom{n}{k}",
        r"\tbinom{n}{k}",
        r"\dfrac{1}{2}",
        r"\cfrac{1}{1+\cfrac{1}{2}}",
        r"\displaystyle x",
    ],
)
def test_collapsed_groups_render_identically_to_the_uncollapsed_reference(
    chromium_ready, latex
):
    from latex2mathml.converter import convert

    from src.education.remediation.equation_verifier import (
        OfflineMathMLRenderer,
        canonicalize_mathml,
    )

    renderer = OfflineMathMLRenderer()
    raw = convert(latex)
    canonical = canonicalize_mathml(raw)

    assert renderer.render(canonical) == renderer.render(raw)


@pytest.mark.parametrize("latex", [r"\binom{n}{k}", r"\dbinom{n}{k}", r"\tbinom{n}{k}"])
def test_binomial_coefficients_pass_the_real_round_trip(chromium_ready, latex):
    from latex2mathml.converter import convert

    from src.education.remediation.equation_verifier import (
        EquationVerifier,
        OfflineMathMLRenderer,
    )

    renderer = OfflineMathMLRenderer()
    verifier = EquationVerifier(renderer=renderer.render)
    source = _validated(renderer.render(convert(latex)))

    evidence = verifier.verify(source, latex)

    assert evidence.passed is True
    assert evidence.ink_iou >= verifier.config.required_ink_iou


BOX_PADDING_PX = 48  # the formula box carries 24px of padding on every side


def _spacer(width_px: int) -> str:
    return f'<math><mspace width="{width_px}px"/></math>'


def test_formula_exactly_at_the_width_bound_renders(chromium_ready):
    from src.education.remediation.equation_verifier import OfflineMathMLRenderer

    renderer = OfflineMathMLRenderer(max_width=300)

    payload = renderer.render(_spacer(300 - BOX_PADDING_PX))

    with Image.open(BytesIO(payload)) as image:
        assert image.width == 300


def test_formula_one_pixel_over_the_width_bound_reports_the_specific_reason(
    chromium_ready,
):
    from src.education.remediation.equation_verifier import (
        OfflineMathMLRenderer,
        RendererDimensionLimit,
    )

    renderer = OfflineMathMLRenderer(max_width=300)

    with pytest.raises(RendererDimensionLimit, match="^renderer_dimension_limit$"):
        renderer.render(_spacer(300 - BOX_PADDING_PX + 1))
    assert renderer.network_requests == 0


def test_formula_over_the_height_bound_reports_the_specific_reason(chromium_ready):
    from src.education.remediation.equation_verifier import (
        OfflineMathMLRenderer,
        RendererDimensionLimit,
    )

    # A line box is 48px (48px font, line-height 1) plus the box padding.
    height = 48 + BOX_PADDING_PX
    with Image.open(
        BytesIO(OfflineMathMLRenderer(max_height=height).render(_spacer(10)))
    ) as image:
        assert image.height == height
    with pytest.raises(RendererDimensionLimit, match="^renderer_dimension_limit$"):
        OfflineMathMLRenderer(max_height=height - 1).render(_spacer(10))


def test_verifier_reports_an_oversized_formula_and_still_verifies_a_fitting_one(
    chromium_ready,
):
    from src.education.remediation.equation_verifier import (
        EquationVerificationRejected,
        EquationVerifier,
        OfflineMathMLRenderer,
    )

    renderer = OfflineMathMLRenderer(max_width=200)
    narrow = "<math><mi>x</mi></math>"
    wide = '<math><mi>x</mi><mspace width="400px"/></math>'
    source = _validated(renderer.render(narrow))

    fitting = EquationVerifier(converter=lambda latex: narrow, renderer=renderer.render)
    assert fitting.verify(source, "x").passed is True

    oversized = EquationVerifier(converter=lambda latex: wide, renderer=renderer.render)
    with pytest.raises(
        EquationVerificationRejected, match="^renderer_dimension_limit$"
    ):
        oversized.verify(source, "x")


def test_math_fixer_reports_an_oversized_equation_through_the_real_renderer(
    chromium_ready,
):
    from types import SimpleNamespace

    from latex2mathml.converter import convert

    from src.education.remediation.equation_verifier import (
        EquationVerifier,
        OfflineMathMLRenderer,
    )
    from tests.test_image_equation_math_fixer import METADATA, Verifier, fixer

    class Recognizer:
        def __init__(self, latex: str) -> None:
            self.latex = latex

        def recognize(self, payload):
            return SimpleNamespace(
                classification="printed_equation",
                latex=self.latex,
                provider="gemini",
                model="vision-model",
            )

    renderer = OfflineMathMLRenderer(max_width=200)
    source = renderer.render("<math><mi>x</mi></math>")

    def candidate(latex: str, converter):
        value = fixer(Verifier("unused"))
        value.equation_recognizer = Recognizer(latex)
        value.image_source.payload.jpeg_bytes = source
        value.image_source.payload.normalized_sha256 = hashlib.sha256(
            source
        ).hexdigest()
        value.equation_verifier = EquationVerifier(
            converter=converter, renderer=renderer.render
        )
        return value

    # Success path first: the same renderer and bound stage a fitting equation.
    fitting = candidate("x", convert)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )
    assert fitting.error == "image_equation_association_pending"

    oversized = candidate(
        "x",
        lambda latex: '<math><mi>x</mi><mspace width="400px"/></math>',
    )._fix_math_issue(SimpleNamespace(metadata=METADATA))
    assert oversized.success is False
    assert oversized.error == "equation_too_large_for_verification"
    assert oversized.pending_association is None
