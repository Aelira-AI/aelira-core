"""Single-symbol misreadings against the printed equation checks.

The visual round trip is a narrow filter: a reading that differs from the image
by one symbol can still pass it. A printed equation is therefore staged only
after a context-isolated second reading agrees with the first on canonical
MathML. These tests run the corpus in
``tests/fixtures/pdfs/image_equations/single_symbol_edits.json`` through the
whole printed path and assert that none of the wrong readings is staged.

Two harnesses run the same corpus. The fast one replaces the visual filter with a
renderer and comparator that pass every reading, so the second reading is the
only thing that can refuse. The Chromium one renders each truth with the real
renderer, runs each wrong reading through the real visual check and then through
the full path, and records how many wrong readings the visual check passed on its
own. That count is a measurement, not an assertion: it is written to the test
properties and to ``test-results/`` so that CI shows it.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest
from latex2mathml.converter import convert
from PIL import Image

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.math_speech import equation_alt_text_is_ambiguous
from src.education.remediation import math_fixer as math_fixer_module
from src.education.remediation import printed_equation_verifier as verifier_module
from src.education.remediation.equation_verifier import (
    ComparisonMetrics,
    EquationVerificationRejected,
    EquationVerifier,
    OfflineMathMLRenderer,
    canonicalize_mathml,
)
from src.education.remediation.math_fixer import MathFixer

CORPUS_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "pdfs"
    / "image_equations"
    / "single_symbol_edits.json"
)
CALIBRATION_PATH = CORPUS_PATH.with_name("manifest.json")
MEASUREMENT_PATH = Path("test-results") / "printed-equation-single-symbol.json"

REQUIRED_KINDS = {
    "change_digit",
    "swap_sign",
    "swap_fraction",
    "swap_script",
    "change_variable",
    "drop_term",
}
ACCEPTED = "image_equation_association_pending"
DISAGREEMENT = "printed_equation_semantic_disagreement"
VISUAL_FAILED = "equation_verification_failed"

METADATA = {
    "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
    "page_number": 1,
    "image_xref": 7,
    "image_index": 0,
    "occurrence_ordinal": 0,
    "bbox": (1.0, 2.0, 3.0, 4.0),
    "occurrence_id": "occ-1",
}


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Case:
    expression_id: str
    kind: str
    truth: str
    reading: str

    @property
    def label(self) -> str:
        return f"{self.expression_id}/{self.kind}: {self.reading}"


def _load_corpus() -> dict[str, Any]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _cases() -> list[Case]:
    return [
        Case(expression["id"], edit["kind"], expression["truth"], edit["reading"])
        for expression in _load_corpus()["expressions"]
        for edit in expression["edits"]
    ]


def _truths() -> list[tuple[str, str]]:
    return [
        (expression["id"], expression["truth"])
        for expression in _load_corpus()["expressions"]
    ]


CASES = _cases()
TRUTHS = _truths()


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _compact(latex: str) -> str:
    return re.sub(r"\s+", "", latex)


def _canonical(latex: str) -> str:
    return canonicalize_mathml(convert(latex))


def test_the_corpus_spans_every_kind_over_many_multi_term_expressions():
    corpus = _load_corpus()
    expressions = corpus["expressions"]
    # Two or more operators or relations: a sum with a result, not one product.
    multi_term = [
        item for item in expressions if len(re.findall(r"[+\-=<>]", item["truth"])) >= 2
    ]

    assert set(corpus["kinds"]) == REQUIRED_KINDS
    assert len(CASES) >= 30
    assert len(multi_term) >= 8
    assert len({item["id"] for item in expressions}) == len(expressions)
    assert all(item["edits"] for item in expressions)
    for kind in REQUIRED_KINDS:
        assert sum(case.kind == kind for case in CASES) >= 2, kind
    assert ("x^{3} + 3x - 7 = 0", "x^{2} + 3x - 7 = 0") in {
        (case.reading, case.truth) for case in CASES
    }


def test_the_corpus_stays_separate_from_the_calibration_manifest():
    manifest = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    calibration = {item["latex"] for item in manifest["positive_pairs"]}
    for item in manifest["near_miss_pairs"]:
        calibration.update((item["source_latex"], item["changed_latex"]))
    corpus = {case.truth for case in CASES} | {case.reading for case in CASES}

    assert CORPUS_PATH != CALIBRATION_PATH
    assert CORPUS_PATH.parent == CALIBRATION_PATH.parent
    assert calibration.isdisjoint(corpus)
    assert "expressions" not in manifest


def test_every_truth_could_stage_and_every_edit_is_a_distinct_wrong_reading():
    """Only the second reading can refuse a reading the alt-text guard accepts.

    A truth the closed-form guard refuses could never be staged, and an edit the
    guard refuses would be turned away without the second reading, which would
    make a count of accepted edits say nothing about agreement.
    """
    for expression_id, truth in TRUTHS:
        assert equation_alt_text_is_ambiguous(truth) is False, expression_id
    seen: set[str] = set()
    for case in CASES:
        assert equation_alt_text_is_ambiguous(case.reading) is False, case.label
        assert _canonical(case.reading) != _canonical(case.truth), case.label
        assert case.reading not in seen, case.label
        seen.add(case.reading)
    assert not seen & {truth for _, truth in TRUTHS}


def test_each_edit_is_a_small_change_from_its_truth():
    for case in CASES:
        distance = _edit_distance(_compact(case.truth), _compact(case.reading))
        if case.kind == "drop_term":
            assert 0 < distance <= 6, case.label
            assert len(_compact(case.reading)) < len(_compact(case.truth)), case.label
        else:
            assert 0 < distance <= 2, case.label
            assert len(_compact(case.reading)) == len(_compact(case.truth)), case.label


# ---------------------------------------------------------------------------
# One harness for both runs: real recognizer, real second reader, real MathFixer
# ---------------------------------------------------------------------------


def _jpeg_of(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, "JPEG", quality=95, subsampling=0)
    return output.getvalue()


def _raster(jpeg: bytes) -> SimpleNamespace:
    digest = hashlib.sha256(jpeg).hexdigest()
    return SimpleNamespace(
        jpeg_bytes=jpeg,
        mime_type="image/jpeg",
        normalized_sha256=digest,
        source_sha256="c" * 64,
        identity=SimpleNamespace(
            page_number=1,
            image_xref=7,
            image_index=0,
            occurrence_ordinal=0,
            bbox=(1.0, 2.0, 3.0, 4.0),
            occurrence_id="occ-1",
        ),
    )


class _Source:
    def __init__(self, payload: SimpleNamespace) -> None:
        self.payload = payload

    def extract(self, document: Any, metadata: Any) -> SimpleNamespace:
        return self.payload


class _RoutingClient:
    """An alt-text client that answers by prompt and records every call.

    The second reader sends its own pinned prompt, so one client can play both
    parts: the first reading is whatever the recognizer prompt is answered with
    and the second is whatever the verifier prompt is answered with. The client
    never learns the first reading from the second call, exactly like a provider.
    """

    purpose = "alt_text"
    provider = "fixture-provider"
    model = "fixture-vision-v1"

    def __init__(self, first_latex: str, second_latex: str, image: bytes) -> None:
        self.first_latex = first_latex
        self.second_latex = second_latex
        self.image = image
        self.first_calls = 0
        self.second_calls = 0

    def analyze_image_sync(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["image_data"] == self.image
        if kwargs["prompt"] == verifier_module._VERIFIER_PROMPT:
            self.second_calls += 1
            latex = self.second_latex
        else:
            self.first_calls += 1
            latex = self.first_latex
        return {
            "success": True,
            "content": json.dumps(
                {"classification": "printed_equation", "latex": latex}
            ),
        }


@dataclasses.dataclass
class Run:
    result: Any
    client: _RoutingClient

    @property
    def accepted(self) -> bool:
        return (
            self.result.error == ACCEPTED
            and self.result.pending_association is not None
        )


def _run_printed_path(
    jpeg: bytes, first_latex: str, second_latex: str, verifier: EquationVerifier
) -> Run:
    client = _RoutingClient(first_latex, second_latex, jpeg)
    fixer = MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=SimpleNamespace(),
        alt_text_client=client,
        image_source=_Source(_raster(jpeg)),
        equation_verifier=verifier,
    )
    result = fixer._fix_math_issue(SimpleNamespace(metadata=METADATA))
    assert client.first_calls == 1
    return Run(result, client)


def _agreement_disabled() -> Callable[..., Any]:
    """Agreement that compares the first reading with itself.

    The second reading is still requested and still sent, but whatever it says
    is replaced by the first reading before the comparison, so the staging path
    runs with no independent check.
    """
    real = math_fixer_module.printed_equation_agreement

    def echo_first_reading(primary: Any, second: Any, **kwargs: Any) -> Any:
        echoed = dataclasses.replace(
            second,
            classification="printed_equation",
            latex=primary.latex,
            latex_sha256=hashlib.sha256(primary.latex.encode("utf-8")).hexdigest(),
        )
        return real(primary, echoed, **kwargs)

    return echo_first_reading


# ---------------------------------------------------------------------------
# Fast run: a visual check that passes everything
# ---------------------------------------------------------------------------

_BLANK_PNG = io.BytesIO()
Image.new("RGB", (32, 16), "white").save(_BLANK_PNG, "PNG")
_FAST_JPEG = _jpeg_of(Image.new("RGB", (64, 24), "white"))


def _permissive_verifier() -> EquationVerifier:
    return EquationVerifier(
        renderer=lambda mathml: _BLANK_PNG.getvalue(),
        comparator=lambda source, rendered: ComparisonMetrics(
            ink_iou=1.0, pixel_similarity=1.0
        ),
    )


def test_a_permissive_visual_check_passes_every_reading_in_the_corpus():
    verifier = _permissive_verifier()
    source = _raster(_FAST_JPEG)

    for case in CASES:
        evidence = verifier.verify(source, case.reading)
        assert evidence.passed is True, case.label


def test_the_full_printed_path_accepts_none_of_the_wrong_readings():
    verifier = _permissive_verifier()
    accepted = []

    for case in CASES:
        run = _run_printed_path(_FAST_JPEG, case.reading, case.truth, verifier)
        # The visual check let the wrong reading through, so the second reading
        # was asked for it exactly once and is what refused it.
        assert run.client.second_calls == 1, case.label
        assert run.result.error == DISAGREEMENT, case.label
        assert run.result.pending_association is None, case.label
        assert run.result.consensus_evidence is None, case.label
        if run.accepted:
            accepted.append(case.label)

    assert len(CASES) >= 30
    assert accepted == [], f"{len(accepted)} of {len(CASES)} accepted: {accepted}"


def test_the_truthful_reading_of_each_expression_is_accepted_by_the_same_path():
    """Positive control: the harness can reach acceptance, so a refusal means something."""
    verifier = _permissive_verifier()

    for expression_id, truth in TRUTHS:
        run = _run_printed_path(_FAST_JPEG, truth, truth, verifier)
        assert run.accepted, expression_id
        assert run.client.second_calls == 1, expression_id
        assert run.result.consensus_evidence is not None, expression_id
        assert run.result.needs_review is True, expression_id


def test_with_agreement_disabled_the_same_path_accepts_every_wrong_reading(
    monkeypatch,
):
    """Negative control: without agreement nothing else stops the wrong readings."""
    monkeypatch.setattr(
        math_fixer_module, "printed_equation_agreement", _agreement_disabled()
    )
    verifier = _permissive_verifier()

    accepted = [
        case.label
        for case in CASES
        if _run_printed_path(_FAST_JPEG, case.reading, case.truth, verifier).accepted
    ]

    assert len(accepted) == len(
        CASES
    ), f"only {len(accepted)} of {len(CASES)} accepted without agreement"


# ---------------------------------------------------------------------------
# Real renderer: the pinned Chromium, the real visual check, the full path
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Outcome:
    case: Case
    visual_passed: bool
    visual_error: Optional[str]
    ink_iou: Optional[float]
    pixel_similarity: Optional[float]
    full_path_error: Optional[str]
    full_path_accepted: bool
    second_reads: int
    accepted_without_agreement: bool


@dataclasses.dataclass
class Measurement:
    outcomes: list[Outcome]
    truths_passed_visual: dict[str, bool]
    truths_accepted: dict[str, bool]
    config: Any
    renders: int

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def visual_only_passed(self) -> int:
        return sum(item.visual_passed for item in self.outcomes)

    @property
    def full_path_accepted(self) -> int:
        return sum(item.full_path_accepted for item in self.outcomes)

    @property
    def accepted_without_agreement(self) -> int:
        return sum(item.accepted_without_agreement for item in self.outcomes)

    def by_kind(self) -> dict[str, dict[str, int]]:
        summary: dict[str, dict[str, int]] = {}
        for item in self.outcomes:
            row = summary.setdefault(
                item.case.kind, {"edits": 0, "visual_only_passed": 0}
            )
            row["edits"] += 1
            row["visual_only_passed"] += int(item.visual_passed)
        return summary

    def as_report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "corpus": CORPUS_PATH.name,
            "claim": (
                "Counts for single-symbol edits of printed equations rendered by "
                "the pinned renderer. A measurement, not a calibration."
            ),
            "threshold_version": self.config.threshold_version,
            "renderer_version": self.config.renderer_version,
            "comparator_version": self.config.comparator_version,
            "required_ink_iou": self.config.required_ink_iou,
            "required_pixel_similarity": self.config.required_pixel_similarity,
            "expressions": len(self.truths_passed_visual),
            "wrong_readings": self.total,
            "visual_check_alone_passed": self.visual_only_passed,
            "visual_check_alone_rejected": self.total - self.visual_only_passed,
            "full_path_accepted": self.full_path_accepted,
            "accepted_without_agreement": self.accepted_without_agreement,
            "by_kind": self.by_kind(),
            "edits": [
                {
                    "expression": item.case.expression_id,
                    "kind": item.case.kind,
                    "truth": item.case.truth,
                    "reading": item.case.reading,
                    "visual_check_passed": item.visual_passed,
                    "visual_check_error": item.visual_error,
                    "ink_iou": item.ink_iou,
                    "pixel_similarity": item.pixel_similarity,
                    "full_path_error": item.full_path_error,
                }
                for item in self.outcomes
            ],
        }


def _measure(render: Callable[[str], bytes], renders: Callable[[], int]) -> Measurement:
    verifier = EquationVerifier(renderer=render)
    sources = {
        expression_id: _raster(_jpeg_of(Image.open(io.BytesIO(render(convert(truth))))))
        for expression_id, truth in TRUTHS
    }
    truths_passed_visual: dict[str, bool] = {}
    truths_accepted: dict[str, bool] = {}
    for expression_id, truth in TRUTHS:
        source = sources[expression_id]
        truths_passed_visual[expression_id] = verifier.verify(source, truth).passed
        truths_accepted[expression_id] = _run_printed_path(
            source.jpeg_bytes, truth, truth, verifier
        ).accepted

    outcomes: list[Outcome] = []
    for case in CASES:
        source = sources[case.expression_id]
        evidence = None
        visual_error = None
        try:
            evidence = verifier.verify(source, case.reading)
        except EquationVerificationRejected as exc:
            visual_error = str(exc)
        run = _run_printed_path(source.jpeg_bytes, case.reading, case.truth, verifier)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                math_fixer_module, "printed_equation_agreement", _agreement_disabled()
            )
            without_agreement = _run_printed_path(
                source.jpeg_bytes, case.reading, case.truth, verifier
            )
        outcomes.append(
            Outcome(
                case=case,
                visual_passed=bool(evidence is not None and evidence.passed),
                visual_error=visual_error,
                ink_iou=None if evidence is None else evidence.ink_iou,
                pixel_similarity=(
                    None if evidence is None else evidence.pixel_similarity
                ),
                full_path_error=run.result.error,
                full_path_accepted=run.accepted,
                second_reads=run.client.second_calls,
                accepted_without_agreement=without_agreement.accepted,
            )
        )
    return Measurement(
        outcomes=outcomes,
        truths_passed_visual=truths_passed_visual,
        truths_accepted=truths_accepted,
        config=verifier.config,
        renders=renders(),
    )


@pytest.fixture(scope="module")
def measurement() -> Measurement:
    """Render every truth and every wrong reading once with the real renderer."""
    renderer = OfflineMathMLRenderer()
    count = 0

    @functools.lru_cache(maxsize=None)
    def render(mathml: str) -> bytes:
        nonlocal count
        count += 1
        return renderer.render(mathml)

    try:
        render(convert(TRUTHS[0][1]))
    except RuntimeError as exc:
        pytest.skip(f"pinned Chromium unavailable in this developer environment: {exc}")
    return _measure(render, lambda: count)


def _summary_line(found: Measurement) -> str:
    return (
        f"printed equation single-symbol edits: {found.total} wrong readings over "
        f"{len(found.truths_passed_visual)} expressions; the visual check alone "
        f"passed {found.visual_only_passed} of {found.total}; the full printed "
        f"path (visual check plus blind second reading) accepted "
        f"{found.full_path_accepted} of {found.total}"
    )


def test_real_renderer_visual_filter_alone_is_recorded_for_single_symbol_edits(
    measurement, record_property, capsys
):
    """Record, never assert, how many wrong readings the visual check passes alone."""
    record_property("single_symbol_wrong_readings", measurement.total)
    record_property("single_symbol_visual_only_passed", measurement.visual_only_passed)
    record_property(
        "single_symbol_visual_only_rejected",
        measurement.total - measurement.visual_only_passed,
    )
    record_property("single_symbol_full_path_accepted", measurement.full_path_accepted)
    record_property(
        "single_symbol_threshold_version", measurement.config.threshold_version
    )
    MEASUREMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEASUREMENT_PATH.write_text(
        json.dumps(measurement.as_report(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with capsys.disabled():
        print(f"\n{_summary_line(measurement)}")

    # The measurement is only meaningful when it covers the whole corpus and the
    # truthful readings pass the same check, so the harness can reach acceptance.
    assert measurement.total == len(CASES) >= 30
    assert all(
        measurement.truths_passed_visual.values()
    ), measurement.truths_passed_visual
    assert json.loads(MEASUREMENT_PATH.read_text(encoding="utf-8"))[
        "wrong_readings"
    ] == len(CASES)


def test_real_renderer_full_path_accepts_no_single_symbol_edit(measurement):
    accepted = [
        item.case.label for item in measurement.outcomes if item.full_path_accepted
    ]

    assert accepted == [], f"{len(accepted)} of {measurement.total}: {accepted}"
    for item in measurement.outcomes:
        if item.visual_passed:
            # Passed the visual check, so the second reading was asked for and
            # is what refused it.
            assert item.full_path_error == DISAGREEMENT, item.case.label
            assert item.second_reads == 1, item.case.label
        else:
            # Refused by the visual check, so no second reading was paid for.
            assert item.full_path_error == VISUAL_FAILED, item.case.label
            assert item.second_reads == 0, item.case.label


def test_real_renderer_truthful_readings_are_accepted_and_agreement_is_what_refuses(
    measurement,
):
    """Positive and negative controls for the real-renderer run."""
    assert all(measurement.truths_accepted.values()), measurement.truths_accepted
    # With agreement disabled the full path is the visual check alone, so it
    # accepts exactly the readings the visual check passed. Everything the path
    # refuses beyond that is refused by the second reading.
    assert measurement.accepted_without_agreement == measurement.visual_only_passed
    assert [item.visual_passed for item in measurement.outcomes] == [
        item.accepted_without_agreement for item in measurement.outcomes
    ]
