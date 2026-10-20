"""A printed equation is staged only when a blind second reading agrees.

The first reading of a printed equation is checked by a visual round trip, which
a reading that is wrong by one symbol can still pass. These tests pin the second
reader (it never sees the first reading and uses its own prompt), the exact
agreement on canonical MathML, every manual reason a failed second reading can
produce, and the refusal to write alternative text the generator cannot get
right.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from latex2mathml.converter import convert
from PIL import Image

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.printed_equation_policy import (
    PRINTED_REQUIRED_AGREEMENT_COUNT,
    PRINTED_VERIFIER_POLICY_SHA256,
    PRINTED_VERIFIER_POLICY_VERSION,
    PRINTED_VERIFIER_PROMPT_SHA256,
)
from src.education.remediation import printed_equation_verifier as verifier_module
from src.education.remediation.equation_recognizer import (
    _PROMPT as PRIMARY_PROMPT,
    _SYSTEM_PROMPT as PRIMARY_SYSTEM_PROMPT,
    EquationRecognizer,
)
from src.education.remediation.equation_verifier import canonicalize_mathml
from src.education.remediation.math_fixer import (
    MathFixer,
    MathVerificationEvidence,
    equation_alt_text_is_ambiguous,
    generate_equation_alt_text,
)
from src.education.remediation.printed_equation_verifier import (
    PrintedEquationConsensusEvidence,
    PrintedEquationSecondReader,
    PrintedEquationSecondReading,
    PrintedEquationVerificationRejected,
    printed_equation_agreement,
    strip_math_delimiters,
)
from tests.printed_equation_fixtures import (
    PRIMARY_RESPONSE_SHA256,
    StubSecondReader,
    primary_reading,
    sha256_text,
)

POLICY_SHA256_PIN = "7eb497dd122950ba4072c2671a1083fe6aa1e40f3ee46ef5b6c6e0932cd85c10"
PROMPT_SHA256_PIN = "1ed37bb104cb3a9e3608b9c8ae2415fa04b064fa0cd1ffc51e44604afe3de006"

METADATA = {
    "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
    "page_number": 1,
    "image_xref": 7,
    "image_index": 0,
    "occurrence_ordinal": 0,
    "bbox": (1.0, 2.0, 3.0, 4.0),
    "occurrence_id": "occ-1",
}


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 24), "white").save(output, "JPEG", quality=95)
    return output.getvalue()


JPEG = _jpeg()
JPEG_SHA256 = hashlib.sha256(JPEG).hexdigest()


def _raster() -> SimpleNamespace:
    return SimpleNamespace(
        jpeg_bytes=JPEG,
        mime_type="image/jpeg",
        normalized_sha256=JPEG_SHA256,
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


class _Client:
    """An alt-text-purpose client that records every call and answers by queue."""

    purpose = "alt_text"
    provider = "fixture-provider"
    model = "fixture-vision-v1"

    def __init__(self, *contents: Any) -> None:
        self.contents = list(contents)
        self.calls: list[dict[str, Any]] = []

    def analyze_image_sync(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.contents.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict):
            return item
        return {"success": True, "content": item}


def _printed(latex: str) -> str:
    return json.dumps({"classification": "printed_equation", "latex": latex})


NOT_EQUATION = '{"classification":"not_equation","latex":null}'


# ---------------------------------------------------------------------------
# The reader and its pinned prompt
# ---------------------------------------------------------------------------


def test_the_reader_has_no_way_to_receive_the_first_reading():
    assert list(inspect.signature(PrintedEquationSecondReader.read).parameters) == [
        "self",
        "source",
    ]
    assert list(inspect.signature(PrintedEquationSecondReader.__init__).parameters) == [
        "self",
        "alt_text_client",
        "max_latex_chars",
        "max_response_chars",
    ]
    assert not hasattr(PrintedEquationSecondReader, "verify")


def test_the_prompt_differs_from_the_recognizer_and_matches_the_policy_pin():
    prompt = verifier_module._VERIFIER_PROMPT

    assert prompt != PRIMARY_PROMPT
    assert prompt != PRIMARY_SYSTEM_PROMPT
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        PRINTED_VERIFIER_PROMPT_SHA256
    )
    assert PRINTED_VERIFIER_PROMPT_SHA256 == PROMPT_SHA256_PIN
    # The cache keys on prompt plus image, so the digests must differ too.
    assert sha256_text(prompt) != sha256_text(PRIMARY_PROMPT)
    # The second reader is told it has seen no other answer.
    assert "have not been shown any other answer" in prompt


def test_the_policy_digest_folds_in_the_prompt_the_canonical_form_and_the_count():
    from src.education.canonical_mathml_version import CANONICAL_MATHML_VERSION

    expected = hashlib.sha256(
        json.dumps(
            {
                "agreement": "exact_canonical_mathml",
                "canonical_mathml_version": CANONICAL_MATHML_VERSION,
                "policy_version": PRINTED_VERIFIER_POLICY_VERSION,
                "required_agreement_count": 2,
                "second_reading": "blind_distinct_prompt",
                "verifier_prompt_sha256": PRINTED_VERIFIER_PROMPT_SHA256,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()

    assert PRINTED_VERIFIER_POLICY_SHA256 == expected == POLICY_SHA256_PIN
    assert PRINTED_VERIFIER_POLICY_VERSION == "printed-equation-consensus-v1"
    assert PRINTED_REQUIRED_AGREEMENT_COUNT == 2


@pytest.mark.parametrize(
    "primary_latex",
    [
        r"x^{2} + 3x - 7 = 0",
        r"\frac{1}{2} + \frac{1}{3}",
        r"E = mc^{2}",
        r"\int_0^1 x^2 \, dx = \frac{1}{3}",
        r"a_{n+1} = 2 a_{n}",
    ],
)
def test_the_second_prompt_never_contains_the_first_reading(primary_latex: str):
    primary_client = _Client(_printed(primary_latex))
    primary = EquationRecognizer(primary_client).recognize(_raster())
    second_client = _Client(_printed(primary_latex))

    reading = PrintedEquationSecondReader(second_client).read(_raster())

    assert primary.latex == primary_latex
    (call,) = second_client.calls
    assert primary_latex not in call["prompt"]
    assert sha256_text(primary_latex) not in call["prompt"]
    assert call["prompt"] == verifier_module._VERIFIER_PROMPT
    assert call["image_data"] == JPEG
    assert call["prompt"] != primary_client.calls[0]["prompt"]
    assert reading.prompt_sha256 == PRINTED_VERIFIER_PROMPT_SHA256


def test_a_prompt_and_image_cache_cannot_answer_the_second_reading():
    """The provider cache keys on prompt plus image; the prompts must differ."""

    class CachingClient(_Client):
        def __init__(self, answers: dict[str, str]) -> None:
            super().__init__()
            self.answers = answers
            self.cache: dict[tuple[str, str], str] = {}
            self.hits = 0

        def analyze_image_sync(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            key = (kwargs["prompt"], hashlib.sha256(kwargs["image_data"]).hexdigest())
            if key in self.cache:
                self.hits += 1
            else:
                self.cache[key] = self.answers[kwargs["prompt"]]
            return {"success": True, "content": self.cache[key]}

    client = CachingClient(
        {
            PRIMARY_PROMPT: _printed("x^{3} + 1 = 0"),
            verifier_module._VERIFIER_PROMPT: _printed("x^{2} + 1 = 0"),
        }
    )
    primary = EquationRecognizer(client).recognize(_raster())
    reading = PrintedEquationSecondReader(client).read(_raster())

    assert client.hits == 0
    assert primary.latex == "x^{3} + 1 = 0"
    assert reading.latex == "x^{2} + 1 = 0"
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^semantic_disagreement$"
    ):
        printed_equation_agreement(
            SimpleNamespace(
                classification="printed_equation",
                latex=primary.latex,
                provider="p",
                model="m",
                response_sha256=primary.response_sha256,
            ),
            reading,
        )


def test_the_recognition_carries_the_digest_of_the_answer_as_received():
    content = _printed("x^{2}")
    result = EquationRecognizer(_Client(content)).recognize(_raster())

    assert result.response_sha256 == sha256_text(content)


def test_the_reading_records_provider_model_and_digests_of_the_raw_answer():
    content = _printed("x^{2} + 1 = 0")
    reading = PrintedEquationSecondReader(_Client(content)).read(_raster())

    assert reading == PrintedEquationSecondReading(
        classification="printed_equation",
        latex="x^{2} + 1 = 0",
        source_sha256=JPEG_SHA256,
        provider="fixture-provider",
        model="fixture-vision-v1",
        response_sha256=sha256_text(content),
        latex_sha256=sha256_text("x^{2} + 1 = 0"),
        prompt_sha256=PRINTED_VERIFIER_PROMPT_SHA256,
    )


def test_the_reading_prefers_the_identity_the_provider_reported():
    client = _Client(
        {
            "success": True,
            "content": _printed("x"),
            "provider": "reported-provider",
            "model": "reported-model",
        }
    )

    reading = PrintedEquationSecondReader(client).read(_raster())

    assert (reading.provider, reading.model) == ("reported-provider", "reported-model")


def test_the_reader_accepts_latex_with_single_backslashes():
    content = (
        r'{"classification":"printed_equation","latex":"\frac{1}{2} \approx \beta"}'
    )

    reading = PrintedEquationSecondReader(_Client(content)).read(_raster())

    assert reading.latex == r"\frac{1}{2} \approx \beta"
    assert reading.response_sha256 == sha256_text(content)


def test_a_not_equation_answer_is_a_reading_that_declines():
    reading = PrintedEquationSecondReader(_Client(NOT_EQUATION)).read(_raster())

    assert reading.classification == "not_equation"
    assert reading.latex is None
    assert reading.latex_sha256 is None
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^verifier_declined$"
    ):
        printed_equation_agreement(primary_reading("x^2 + 1 = 0"), reading)


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (None, "alt_text_client_unavailable"),
        (_Client(_printed("x")), "purpose_mismatch"),
        (_Client(RuntimeError("secret provider payload")), "provider_failure"),
        (
            _Client({"success": False, "error": "audit_write_failed"}),
            "provider_failure",
        ),
        (_Client({"success": True}), "invalid_provider_response"),
        (
            _Client('```json\n{"classification":"printed_equation","latex":"x"}\n```'),
            "invalid_provider_response",
        ),
        (
            _Client('{"classification":"printed_equation","latex":"x","latex":"y"}'),
            "invalid_provider_response",
        ),
        (
            _Client('{"classification":"printed_equation","latex":"x","extra":1}'),
            "invalid_provider_response",
        ),
        (
            _Client('{"classification":"printed_equation","latex":"x​"}'),
            "invalid_provider_response",
        ),
    ],
)
def test_the_reader_rejects_unusable_clients_and_answers(client, reason):
    if client is not None and reason == "purpose_mismatch":
        client.purpose = "remediation"
    with pytest.raises(
        PrintedEquationVerificationRejected, match=f"^{reason}$"
    ) as raised:
        PrintedEquationSecondReader(client).read(_raster())

    assert raised.value.__cause__ is None
    assert "secret provider payload" not in str(raised.value)
    if client is not None and reason == "purpose_mismatch":
        assert client.calls == []


def test_the_reader_needs_a_provider_identity():
    class Anonymous(_Client):
        provider = None
        model = None

    with pytest.raises(
        PrintedEquationVerificationRejected, match="^provider_identity_unavailable$"
    ):
        PrintedEquationSecondReader(Anonymous(_printed("x"))).read(_raster())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raster: setattr(raster, "jpeg_bytes", b"not a jpeg"),
        lambda raster: setattr(raster, "mime_type", "image/png"),
        lambda raster: setattr(raster, "normalized_sha256", "0" * 64),
    ],
)
def test_the_reader_refuses_an_image_that_is_not_the_proven_source(mutation):
    raster = _raster()
    mutation(raster)
    client = _Client(_printed("x"))

    with pytest.raises(
        PrintedEquationVerificationRejected, match="^invalid_image_payload$"
    ):
        PrintedEquationSecondReader(client).read(raster)
    assert client.calls == []


def test_an_unpinned_or_shared_prompt_is_refused_before_any_provider_call(monkeypatch):
    client = _Client(_printed("x"))
    monkeypatch.setattr(verifier_module, "_VERIFIER_PROMPT", "A different prompt.")
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^verifier_prompt_unpinned$"
    ):
        PrintedEquationSecondReader(client).read(_raster())
    monkeypatch.undo()

    monkeypatch.setattr(
        verifier_module, "_PRIMARY_PROMPT", verifier_module._VERIFIER_PROMPT
    )
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^verifier_prompt_not_distinct$"
    ):
        PrintedEquationSecondReader(client).read(_raster())
    assert client.calls == []


# ---------------------------------------------------------------------------
# Exact agreement on canonical MathML
# ---------------------------------------------------------------------------


def _reading(latex: str, **overrides: Any) -> PrintedEquationSecondReading:
    values: dict[str, Any] = {
        "classification": "printed_equation",
        "latex": latex,
        "source_sha256": JPEG_SHA256,
        "provider": "fixture-provider",
        "model": "fixture-vision-v1",
        "response_sha256": sha256_text(f"second:{latex}"),
        "latex_sha256": sha256_text(latex),
        "prompt_sha256": PRINTED_VERIFIER_PROMPT_SHA256,
    }
    values.update(overrides)
    return PrintedEquationSecondReading(**values)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("x^{2}", "x^2"),
        (r"\frac{1}{2}", r"\frac12"),
        (r"\sqrt{x}", r"\sqrt x"),
        ("x_{i}^{2}", "x_i^2"),
        ("{x}+1", "x+1"),
        ("{{x}}", "x"),
        ("$x^2 + 1$", "x^{2} + 1"),
        (r"\(a+b\)", "a+b"),
        (r"\[ y = 2 \]", "y=2"),
    ],
)
def test_agreement_across_a_braces_only_difference(first: str, second: str):
    evidence = printed_equation_agreement(primary_reading(first), _reading(second))

    assert isinstance(evidence, PrintedEquationConsensusEvidence)
    assert evidence.passed is True
    assert evidence.mathml_sha256 == evidence.primary_mathml_sha256
    assert evidence.mathml_sha256 == evidence.verifier_mathml_sha256
    assert evidence.primary_latex_sha256 == sha256_text(first)
    assert evidence.verifier_latex_sha256 == sha256_text(second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("x^2", "x^3"),
        (r"\frac12", r"\frac21"),
        ("a-b", "a+b"),
        ("x^{2} + 3x - 7 = 0", "x^{2} + 3x - 1 = 0"),
        (r"\frac{1}{2}", r"\frac{1}{3}"),
        ("n(n-1)", "n(n+1)"),
        ("x_1", "x_2"),
        ("a = b", "a \\neq b"),
        ("\\sin x", "\\cos x"),
        ("x < y", "x > y"),
        ("2x + 1", "2x"),
    ],
)
def test_a_single_symbol_difference_is_a_disagreement(first: str, second: str):
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^semantic_disagreement$"
    ):
        printed_equation_agreement(primary_reading(first), _reading(second))


def test_a_second_reading_that_cannot_become_passive_mathml_is_a_disagreement():
    mathml = {
        "x^2": "<math><msup><mi>x</mi><mn>2</mn></msup></math>",
        # mtext is not part of the passive form the verifier accepts.
        "x^3": "<math><mtext>x cubed</mtext></math>",
    }

    with pytest.raises(
        PrintedEquationVerificationRejected, match="^semantic_disagreement$"
    ):
        printed_equation_agreement(
            primary_reading("x^2"), _reading("x^3"), converter=mathml.__getitem__
        )
    # Positive control: the same converter agrees when both readings convert.
    assert printed_equation_agreement(
        primary_reading("x^2"), _reading("x^2"), converter=mathml.__getitem__
    ).passed


def test_a_primary_that_cannot_become_passive_mathml_is_a_failure_not_a_disagreement():
    mathml = {"x^2": "<math><mtext>x</mtext></math>"}

    with pytest.raises(
        PrintedEquationVerificationRejected, match="^primary_conversion_failed$"
    ):
        printed_equation_agreement(
            primary_reading("x^2"), _reading("x^2"), converter=mathml.__getitem__
        )


def test_the_agreement_evidence_names_both_readings_and_the_policy():
    first = primary_reading("x^{2}", provider="primary-provider", model="primary-model")
    second = _reading("x^2", provider="second-provider", model="second-model")

    evidence = printed_equation_agreement(first, second)

    assert evidence.policy_version == PRINTED_VERIFIER_POLICY_VERSION
    assert evidence.policy_sha256 == PRINTED_VERIFIER_POLICY_SHA256
    assert evidence.agreement_count == evidence.required_agreement_count == 2
    assert evidence.verifier_prompt_sha256 == PRINTED_VERIFIER_PROMPT_SHA256
    assert evidence.source_sha256 == JPEG_SHA256
    assert evidence.primary_response_sha256 == PRIMARY_RESPONSE_SHA256
    assert evidence.verifier_response_sha256 == second.response_sha256
    assert (evidence.primary_provider, evidence.primary_model) == (
        "primary-provider",
        "primary-model",
    )
    assert (evidence.verifier_provider, evidence.verifier_model) == (
        "second-provider",
        "second-model",
    )
    assert evidence.mathml_sha256 == sha256_text(canonicalize_mathml(convert("x^{2}")))


@pytest.mark.parametrize(
    "primary",
    [
        primary_reading("x^2", provider=None),
        primary_reading("x^2", model=None),
        primary_reading("x^2", provider=" padded "),
        primary_reading("x^2", response_sha256=None),
        primary_reading("x^2", response_sha256="short"),
        primary_reading("x^2", response_sha256="A" * 64),
        SimpleNamespace(
            classification="not_equation",
            latex=None,
            provider="p",
            model="m",
            response_sha256=PRIMARY_RESPONSE_SHA256,
        ),
        primary_reading(""),
        SimpleNamespace(),
    ],
)
def test_agreement_refuses_an_incomplete_first_reading(primary: Any):
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^primary_evidence_incomplete$"
    ):
        printed_equation_agreement(primary, _reading("x^2"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_sha256": "bad"},
        {"response_sha256": "bad"},
        {"latex_sha256": "0" * 64},
        {"provider": ""},
        {"model": ""},
    ],
)
def test_agreement_refuses_a_malformed_second_reading(overrides: dict[str, Any]):
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^second_reading_invalid$"
    ):
        printed_equation_agreement(primary_reading("x^2"), _reading("x^2", **overrides))
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^second_reading_invalid$"
    ):
        printed_equation_agreement(primary_reading("x^2"), SimpleNamespace())


def test_agreement_refuses_a_second_reading_with_no_latex():
    empty = dataclasses.replace(_reading("x^2"), latex="")

    with pytest.raises(
        PrintedEquationVerificationRejected, match="^second_reading_invalid$"
    ):
        printed_equation_agreement(primary_reading("x^2"), empty)


def test_agreement_refuses_a_second_reading_from_another_prompt():
    with pytest.raises(
        PrintedEquationVerificationRejected, match="^verifier_prompt_unpinned$"
    ):
        printed_equation_agreement(
            primary_reading("x^2"),
            _reading("x^2", prompt_sha256=sha256_text(PRIMARY_PROMPT)),
        )


def test_agreement_calls_no_provider_and_is_deterministic():
    first = primary_reading("x^{2} + 1")
    second = _reading("x^2 + 1")

    assert printed_equation_agreement(first, second) == printed_equation_agreement(
        first, second
    )


def test_strip_math_delimiters_matches_the_conversion_the_staged_equation_uses():
    assert strip_math_delimiters("$$ a $$") == "a"
    assert strip_math_delimiters(r"\[ a \]") == "a"
    assert strip_math_delimiters(r"\( a \)") == "a"
    assert strip_math_delimiters("$ a $") == "a"
    assert strip_math_delimiters("a") == "a"
    assert strip_math_delimiters("$") == "$"


# ---------------------------------------------------------------------------
# MathFixer: when the second reading runs, and what each outcome stages
# ---------------------------------------------------------------------------


class _Source:
    def __init__(self) -> None:
        self.payload = _raster()

    def extract(self, document: Any, metadata: Any) -> Any:
        return self.payload


class _Recognizer:
    def __init__(self, latex: str) -> None:
        self.latex = latex
        self.calls = 0

    def recognize(self, payload: Any) -> Any:
        self.calls += 1
        return primary_reading(self.latex)


class _Verifier:
    """The visual check: passes or fails on demand over the canonical MathML."""

    def __init__(self, passes: bool = True) -> None:
        self.passes = passes
        self.calls = 0

    def canonicalize_mathml(self, mathml: str) -> str:
        return canonicalize_mathml(mathml)

    def verify(self, image: Any, latex: str) -> MathVerificationEvidence:
        self.calls += 1
        mathml = self.canonicalize_mathml(convert(strip_math_delimiters(latex)))
        return MathVerificationEvidence(
            passed=self.passes,
            source_sha256=image.normalized_sha256,
            rendered_sha256="b" * 64,
            mathml_sha256=sha256_text(mathml),
            renderer_version="renderer-v1",
            comparator_version="comparator-v1",
            font_sha256="f" * 64,
            threshold_version="threshold-v1",
            ink_iou=1.0,
            pixel_similarity=1.0,
            required_ink_iou=0.9,
            required_pixel_similarity=0.98,
        )


def _fixer(
    primary_latex: str,
    reader: Any,
    *,
    passes: bool = True,
    alt_text_client: Any = None,
) -> MathFixer:
    return MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=SimpleNamespace(),
        alt_text_client=alt_text_client,
        image_source=_Source(),
        equation_recognizer=_Recognizer(primary_latex),
        equation_verifier=_Verifier(passes),
        printed_equation_second_reader=reader,
    )


def _fix(fixer: MathFixer) -> Any:
    return fixer._fix_math_issue(SimpleNamespace(metadata=METADATA))


def test_zero_second_readings_when_the_visual_check_fails():
    reader = StubSecondReader("x^2 + 1 = 0")
    fixer = _fixer("x^2 + 1 = 0", reader, passes=False)

    result = _fix(fixer)

    assert result.error == "equation_verification_failed"
    assert result.pending_association is None
    assert result.consensus_evidence is None
    assert reader.calls == 0
    assert fixer.equation_verifier.calls == 1


def test_exactly_one_second_reading_when_the_visual_check_passes():
    reader = StubSecondReader("x^2 + 1 = 0")
    fixer = _fixer("x^2 + 1 = 0", reader)

    result = _fix(fixer)

    assert result.error == "image_equation_association_pending"
    assert reader.calls == 1
    assert reader.sources == [fixer.image_source.payload]
    assert result.consensus_evidence is not None
    assert result.pending_association.consensus_evidence == result.consensus_evidence
    assert result.consensus_evidence.mathml_sha256 == (
        result.verification_evidence.mathml_sha256
    )
    assert result.consensus_evidence.mathml_sha256 == sha256_text(
        result.pending_association.mathml_string
    )
    assert result.consensus_evidence.source_sha256 == JPEG_SHA256


def test_exactly_one_provider_call_through_the_real_reader():
    client = _Client(_printed("x^{2} + 1 = 0"))
    fixer = _fixer("x^2 + 1 = 0", None, alt_text_client=client)

    result = _fix(fixer)

    assert result.error == "image_equation_association_pending"
    assert len(client.calls) == 1
    assert client.calls[0]["prompt"] == verifier_module._VERIFIER_PROMPT
    assert "x^2 + 1 = 0" not in client.calls[0]["prompt"]


def test_the_visual_check_still_runs_first_and_unchanged_on_a_failed_reading():
    client = _Client(_printed("x^{2} + 1 = 0"))
    fixer = _fixer("x^2 + 1 = 0", None, passes=False, alt_text_client=client)

    result = _fix(fixer)

    assert result.error == "equation_verification_failed"
    assert client.calls == []


def test_a_braces_only_difference_stages_the_equation_with_the_first_alt_text():
    reader = StubSecondReader("x^2 + 3x - 7 = 0")
    result = _fix(_fixer("x^{2} + 3x - 7 = 0", reader))

    assert result.error == "image_equation_association_pending"
    assert result.aria_label == "x squared + 3x - 7 = 0"
    assert result.needs_review is True
    assert result.confidence == 0.55
    assert result.pending_association is not None


@pytest.mark.parametrize(
    ("reader", "error"),
    [
        (None, "printed_equation_verifier_unavailable"),
        (
            StubSecondReader(error=RuntimeError("provider down")),
            "printed_equation_verifier_failed",
        ),
        (
            StubSecondReader(
                error=PrintedEquationVerificationRejected("provider_failure")
            ),
            "printed_equation_verifier_failed",
        ),
        (
            StubSecondReader(
                error=PrintedEquationVerificationRejected("invalid_provider_response")
            ),
            "printed_equation_verifier_failed",
        ),
        (
            StubSecondReader(classification="not_equation"),
            "printed_equation_verifier_declined",
        ),
        (
            StubSecondReader("x^3 + 1 = 0"),
            "printed_equation_semantic_disagreement",
        ),
        (
            StubSecondReader("x^{2} + 1 = 0 +"),
            "printed_equation_semantic_disagreement",
        ),
    ],
)
def test_each_failed_second_reading_stays_manual_with_its_own_code(reader, error):
    result = _fix(_fixer("x^2 + 1 = 0", reader))

    assert result.success is False
    assert result.error == error
    assert result.pending_association is None
    assert result.consensus_evidence is None
    assert result.has_mathml is False
    # The image was recognised as an equation, so no generic description of it
    # may stand in for the equation that could not be confirmed.
    assert result.equation_recognized is True


def test_an_unconfigured_second_reader_is_unavailable_not_a_crash():
    fixer = _fixer("x^2 + 1 = 0", None, alt_text_client=None)

    assert fixer.printed_equation_second_reader is None
    assert _fix(fixer).error == "printed_equation_verifier_unavailable"


def test_a_client_builds_the_real_second_reader_by_default():
    fixer = _fixer("x^2", None, alt_text_client=_Client())

    assert isinstance(fixer.printed_equation_second_reader, PrintedEquationSecondReader)


def test_a_consensus_for_another_image_or_equation_never_stages():
    class WrongSource(StubSecondReader):
        def read(self, source: Any) -> PrintedEquationSecondReading:
            reading = super().read(source)
            return PrintedEquationSecondReading(
                **{**reading.__dict__, "source_sha256": "9" * 64}
            )

    result = _fix(_fixer("x^2 + 1 = 0", WrongSource("x^2 + 1 = 0")))

    assert result.error == "equation_verification_mismatch"
    assert result.pending_association is None


def test_the_second_reading_cannot_bind_a_different_staged_equation(monkeypatch):
    """The agreed digest must be the digest of the MathML that is staged."""
    from src.education.remediation import math_fixer

    real = math_fixer.printed_equation_agreement

    def other_digest(primary: Any, second: Any) -> PrintedEquationConsensusEvidence:
        evidence = real(primary, second)
        return PrintedEquationConsensusEvidence(
            **{**evidence.__dict__, "mathml_sha256": "8" * 64}
        )

    monkeypatch.setattr(math_fixer, "printed_equation_agreement", other_digest)

    result = _fix(_fixer("x^2 + 1 = 0", StubSecondReader("x^2 + 1 = 0")))

    assert result.error == "equation_verification_mismatch"
    assert result.pending_association is None


# A reading wrong by one symbol, passed by a permissive visual check, with the
# truthful second reading. None may be staged.
SINGLE_SYMBOL_EDITS = [
    ("x^{3} + 3x - 7 = 0", "x^{2} + 3x - 7 = 0"),
    (r"\frac{1}{2} + y = 4", r"\frac{1}{3} + y = 4"),
    ("n(n+1) = 2k", "n(n-1) = 2k"),
    ("a + b = c", "a - b = c"),
    ("x_{1} + x_{2} = 0", "x_{1} + x_{3} = 0"),
    ("y = 2x + 1", "y = 2x + 7"),
    (r"\sqrt{x} + 1 = 3", r"\sqrt{x} - 1 = 3"),
    ("a < b", "a > b"),
]


@pytest.mark.parametrize(("wrong", "truth"), SINGLE_SYMBOL_EDITS)
def test_a_reading_wrong_by_one_symbol_is_never_staged(wrong: str, truth: str):
    reader = StubSecondReader(truth)
    fixer = _fixer(wrong, reader)

    result = _fix(fixer)

    # The permissive visual check accepted the wrong reading.
    assert fixer.equation_verifier.calls == 1
    assert reader.calls == 1
    assert result.error == "printed_equation_semantic_disagreement"
    assert result.pending_association is None
    assert result.consensus_evidence is None
    assert result.verification_evidence is None


@pytest.mark.parametrize(("wrong", "truth"), SINGLE_SYMBOL_EDITS)
def test_the_truthful_reading_of_the_same_image_is_staged(wrong: str, truth: str):
    result = _fix(_fixer(truth, StubSecondReader(truth)))

    assert result.error == "image_equation_association_pending"
    assert result.pending_association is not None


# ---------------------------------------------------------------------------
# Alternative text the generator cannot get right is refused, never guessed
# ---------------------------------------------------------------------------

AMBIGUOUS = [
    r"\frac12",
    r"\dfrac12",
    r"\frac{1}2",
    r"\sqrt x",
    r"\sqrt2",
    r"\sqrt[3]x",
    r"\binom nk",
    r"x^10",
    r"a_ij",
    r"x^2y",
    r"x^\pi",
    r"x^ 2",
    r"x^",
    r"\frac{x^{2}}{y}",
    r"\sqrt{x^{2}+1}",
    r"e^{x^{2}}",
    r"x^{}",
    "x",
    "$x$",
    # A compound argument reads the same as a group that ends earlier.
    r"\sqrt{x+1}",
    r"a_{ij}",
    r"3x^{2}y + 2xy^{2} = 0",
    r"y = \frac{1}{2}x + 3",
    r"x^{n}y",
    r"x^{ny}",
    r"\sqrt{2}x",
    r"\sqrt{2x}",
    r"a_{n}x^{n}",
    r"\bar{x} = 5",
    r"\hat{x} + 1",
    r"\sqrt{x+1} = 3",
    r"\frac{a+b}{c}",
    r"x^{n+1}",
]
UNAMBIGUOUS = [
    r"x^{2} + 3x - 7 = 0",
    r"x^2 + 1 = 0",
    r"\frac{1}{2}",
    r"\dfrac{1}{2}",
    r"\sqrt[3]{x}",
    r"\binom{n}{k}",
    r"\dbinom{n}{k}",
    r"x_i^2",
    r"\int_0^1 x^2 dx",
    r"E = mc^{2}",
    r"$x^2 + 1$",
    r"\(a+b\)",
    r"a-b",
    r"x=y",
    r"x^{2}+y^{2}=r^{2}",
    r"\frac{a}{b}+\frac{c}{d}",
]


@pytest.mark.parametrize("latex", AMBIGUOUS)
def test_ambiguous_latex_is_detected(latex: str):
    assert equation_alt_text_is_ambiguous(latex) is True


@pytest.mark.parametrize("latex", UNAMBIGUOUS)
def test_translatable_latex_is_not_flagged(latex: str):
    assert equation_alt_text_is_ambiguous(latex) is False
    assert generate_equation_alt_text(latex) != "mathematical equation"


def test_the_generic_fallback_is_always_ambiguous():
    for latex in ("x", "a", "7", "$y$"):
        assert generate_equation_alt_text(latex) == "mathematical equation"
        assert equation_alt_text_is_ambiguous(latex) is True


def test_a_binomial_reads_as_a_choice():
    assert generate_equation_alt_text(r"\binom{n}{k}") == "n choose k"
    assert generate_equation_alt_text(r"\dbinom{n}{k}") == "n choose k"
    assert generate_equation_alt_text(r"\tbinom{a+1}{2}") == "a+1 choose 2"
    assert generate_equation_alt_text(r"\binom{n}{k} + 1") == "n choose k + 1"
    assert generate_equation_alt_text(r"\dfrac{1}{2}") == "1 over 2"
    assert generate_equation_alt_text(r"\frac{1}{2}") == "1 over 2"


@pytest.mark.parametrize(
    ("latex", "words"),
    [
        (r"x \le 3", "x less than or equal to 3"),
        (r"x \ne 0", "x not equal to 0"),
        (r"\sin x = 0", "sine x = 0"),
        (r"\log_{2} 8 = 3", "log sub 2 8 = 3"),
        (r"a \pm b", "a plus or minus b"),
    ],
)
def test_a_reading_with_a_named_command_is_staged_with_its_words(latex, words):
    result = _fix(_fixer(latex, StubSecondReader(latex)))

    assert result.error == "image_equation_association_pending"
    assert result.aria_label == words
    assert result.pending_association.alt_text == words


def test_a_staged_binomial_carries_the_choice_wording():
    result = _fix(_fixer(r"\binom{n}{k}", StubSecondReader(r"\binom{n}{k}")))

    assert result.error == "image_equation_association_pending"
    assert result.aria_label == "n choose k"
    assert result.pending_association.alt_text == "n choose k"


# The ambiguous readings that the MathML converter accepts, so that they reach
# the alternative-text check at all.
CONVERTIBLE_AMBIGUOUS = [
    r"\frac12",
    r"\dfrac12",
    r"\sqrt x",
    r"\sqrt2",
    r"\binom nk",
    r"x^10",
    r"a_ij",
    r"x^2y",
    r"x^\pi",
    r"\frac{x^{2}}{y}",
    r"e^{x^{2}}",
    "x",
    "$x$",
    # Readings whose words run together or erase a command, or whose argument
    # holds more than one token and so does not say where it ends.
    r"3x^{2}y + 2xy^{2} = 0",
    r"y = \frac{1}{2}x + 3",
    r"x^{n}y",
    r"x^{ny}",
    r"\sqrt{2}x",
    r"\sqrt{2x}",
    r"a_{n}x^{n}",
    r"\bar{x} = 5",
    r"\hat{x} + 1",
    r"\sqrt{x+1} = 3",
    r"\frac{a+b}{c}",
    r"x^{n+1}",
]


@pytest.mark.parametrize("latex", CONVERTIBLE_AMBIGUOUS)
def test_an_ambiguous_first_reading_is_refused_before_the_second_provider_call(latex):
    assert set(CONVERTIBLE_AMBIGUOUS) <= set(AMBIGUOUS)
    reader = StubSecondReader(latex)

    result = _fix(_fixer(latex, reader))

    assert result.error == "image_equation_alt_text_ambiguous"
    assert result.pending_association is None
    assert result.consensus_evidence is None
    assert reader.calls == 0


def test_an_ambiguous_second_reading_is_refused_even_when_the_first_is_clean():
    # Same equation, different notation: the second reading writes \frac12.
    reader = StubSecondReader(r"\frac12 + y = 4")
    result = _fix(_fixer(r"\frac{1}{2} + y = 4", reader))

    assert reader.calls == 1
    assert result.error == "image_equation_alt_text_ambiguous"
    assert result.pending_association is None
    assert result.consensus_evidence is None


def test_a_clean_pair_is_not_refused_for_alt_text():
    result = _fix(
        _fixer(r"\frac{1}{2} + y = 4", StubSecondReader(r"\frac{1}{2} + y = 4"))
    )

    assert result.error == "image_equation_association_pending"
    assert result.aria_label == "1 over 2 + y = 4"


# ---------------------------------------------------------------------------
# End to end: the real scanner, remediator and association writer
# ---------------------------------------------------------------------------


@pytest.fixture
def install_verifier(monkeypatch: pytest.MonkeyPatch):
    """Replace only the browser round trip; the second reading stays real."""
    from src.education.remediation import pdf_remediator
    from tests.test_equation_alt_text_coordination import _Verifier as BrowserStub

    def install() -> None:
        class StubbedMathFixer(MathFixer):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("equation_verifier", BrowserStub(True))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(pdf_remediator, "MathFixer", StubbedMathFixer)

    return install


def test_a_disagreeing_second_reading_leaves_no_formula_and_no_description(
    tmp_path, install_verifier
):
    from src.education.remediation.image_equation_gate import (
        contains_image_equation_fixes,
    )
    from src.education.remediation.outcome_explanations import manual_explanation
    from tests.test_equation_alt_text_coordination import (
        EQUATION_INDEX,
        _issue_ids,
        _manual,
        _Provider,
        _run,
        _structure,
        _write_untagged_source,
    )

    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    # The first reading says x squared; the blind second reading says x cubed.
    provider = _Provider(second_latex_by_width={120: "x^{3} + 1 = 0"})

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        ids = _issue_ids(remediator, EQUATION_INDEX)
        manual = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert manual.reason == "printed_equation_semantic_disagreement"
        assert manual_explanation(manual)["reason_code"] == (
            "printed_equation_semantic_disagreement"
        )
        withheld = _manual(result, ids["missing_alt_text"])
        assert withheld.reason == "image_equation_alt_text_withheld"
        assert provider.kinds.count("recognition") == 1
        assert provider.kinds.count("second_reading") == 1
        structure = _structure(result.output_file)
        assert [alt for role, alt in structure if role == "/Formula"] == []
        assert not contains_image_equation_fixes(result.fixed_issues)
        # Neither reading's description is carried by a structure element of any
        # role. The probe below finds it when the readings agree.
        assert _roles_carrying_alt(structure, "squared", "cubed") == []
    finally:
        result.close_output_claim()


def _roles_carrying_alt(structure, *words: str) -> list[str]:
    """The role of every structure element whose alt text holds any of the words."""
    return [
        role for role, alt in structure if alt and any(word in alt for word in words)
    ]


def test_an_agreeing_second_reading_writes_a_description_the_probe_finds(
    tmp_path, install_verifier
):
    from tests.test_equation_alt_text_coordination import (
        EQUATION_ALT,
        _Provider,
        _run,
        _structure,
        _write_untagged_source,
    )

    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)

    _remediator, result = _run(source, tmp_path, _Provider())
    try:
        assert result.success, result.error_message
        structure = _structure(result.output_file)
        assert _roles_carrying_alt(structure, "squared", "cubed") == ["/Formula"]
        assert [alt for role, alt in structure if role == "/Formula"] == [EQUATION_ALT]
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# Clients that name their model in the answer, not in an attribute
# ---------------------------------------------------------------------------


class _LmsLikeClient:
    """Like the LMS remediation client: a provider attribute and no model.

    The model that answered is named only in the response.
    """

    purpose = "alt_text"
    provider = "lms-remediation"

    def __init__(self, content: str, **response: Any) -> None:
        self.content = content
        self.response = response
        self.calls = 0

    def analyze_image_sync(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {"success": True, "content": self.content, **self.response}


def test_the_first_reading_takes_its_identity_from_the_provider_response():
    client = _LmsLikeClient(
        _printed("x^2 + 1 = 0"), provider="routed-provider", model="routed-model-v2"
    )

    recognition = EquationRecognizer(client).recognize(_raster())

    assert recognition.provider == "routed-provider"
    assert recognition.model == "routed-model-v2"


def test_the_client_attributes_are_the_fallback_for_the_identity():
    client = _LmsLikeClient(_printed("x^2 + 1 = 0"), model="response-model")

    recognition = EquationRecognizer(client).recognize(_raster())

    # No provider in the response, so the client's own attribute is used.
    assert recognition.provider == "lms-remediation"
    assert recognition.model == "response-model"
    attribute_client = _Client(_printed("x^2 + 1 = 0"))
    recognition = EquationRecognizer(attribute_client).recognize(_raster())
    assert (recognition.provider, recognition.model) == (
        "fixture-provider",
        "fixture-vision-v1",
    )


def test_an_unusable_response_identity_falls_back_to_the_attribute():
    client = _LmsLikeClient(_printed("x^2"), provider="", model="bad\x00model")

    recognition = EquationRecognizer(client).recognize(_raster())

    assert recognition.provider == "lms-remediation"
    assert recognition.model is None


def test_a_client_that_names_its_model_in_the_answer_stages_with_one_second_reading():
    client = _LmsLikeClient(_printed("x^2 + 1 = 0"), model="response-model-v1")
    reader = StubSecondReader("x^2 + 1 = 0")
    fixer = _fixer("x^2 + 1 = 0", reader)
    fixer.equation_recognizer = EquationRecognizer(client)

    result = _fix(fixer)

    assert result.error == "image_equation_association_pending"
    assert client.calls == 1
    assert reader.calls == 1
    assert result.model_used == "response-model-v1"
    assert result.provider_used == "lms-remediation"
    assert result.consensus_evidence.primary_model == "response-model-v1"


def test_a_client_with_no_identity_anywhere_is_refused_without_a_second_reading():
    client = _LmsLikeClient(_printed("x^2 + 1 = 0"))
    reader = StubSecondReader("x^2 + 1 = 0")
    fixer = _fixer("x^2 + 1 = 0", reader)
    fixer.equation_recognizer = EquationRecognizer(client)

    result = _fix(fixer)

    assert result.error == "printed_equation_verifier_failed"
    assert result.pending_association is None
    assert result.consensus_evidence is None
    assert reader.calls == 0


@pytest.mark.parametrize(
    "missing",
    [{"provider": None}, {"model": None}, {"response_sha256": None}, {"model": ""}],
)
def test_an_incomplete_first_reading_never_costs_a_second_reading(missing):
    reader = StubSecondReader("x^2 + 1 = 0")
    fixer = _fixer("x^2 + 1 = 0", reader)
    fixer.equation_recognizer = SimpleNamespace(
        recognize=lambda _payload: primary_reading("x^2 + 1 = 0", **missing)
    )

    result = _fix(fixer)

    assert result.error == "printed_equation_verifier_failed"
    assert result.pending_association is None
    assert reader.calls == 0


def test_a_complete_first_reading_is_reported_complete():
    assert verifier_module.primary_reading_is_complete(primary_reading()) is True
    assert verifier_module.primary_reading_is_complete(None) is False
    assert (
        verifier_module.primary_reading_is_complete(
            primary_reading(response_sha256="not-a-digest")
        )
        is False
    )


def test_a_full_flow_with_an_lms_like_client_promotes_the_equation(
    tmp_path, install_verifier
):
    from src.education.remediation.image_equation_gate import (
        contains_image_equation_fixes,
    )
    from tests.test_equation_alt_text_coordination import (
        EQUATION_ALT,
        _Provider,
        _run,
        _structure,
        _write_untagged_source,
    )

    class LmsProvider(_Provider):
        model = None  # the LMS client declares no model

        def analyze_image_sync(self, **kwargs: Any):
            result = super().analyze_image_sync(**kwargs)
            if result.get("success") and result.get("content"):
                result = {**result, "provider": "lms-remediation", "model": "routed-v1"}
            return result

    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = LmsProvider()

    _remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert provider.kinds.count("second_reading") == 1
        assert contains_image_equation_fixes(result.fixed_issues)
        assert any(fix.model_used == "routed-v1" for fix in result.fixed_issues)
        assert [
            alt for role, alt in _structure(result.output_file) if role == "/Formula"
        ] == [EQUATION_ALT]
    finally:
        result.close_output_claim()


def test_a_full_flow_with_a_client_that_names_no_model_pays_for_no_second_reading(
    tmp_path, install_verifier
):
    from src.education.remediation.image_equation_gate import (
        contains_image_equation_fixes,
    )
    from tests.test_equation_alt_text_coordination import (
        EQUATION_INDEX,
        _issue_ids,
        _manual,
        _Provider,
        _run,
        _structure,
        _write_untagged_source,
    )

    class NamelessProvider(_Provider):
        model = None

    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = NamelessProvider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        ids = _issue_ids(remediator, EQUATION_INDEX)
        manual = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert manual.reason == "printed_equation_verifier_failed"
        assert provider.kinds.count("second_reading") == 0
        assert not contains_image_equation_fixes(result.fixed_issues)
        assert [
            alt for role, alt in _structure(result.output_file) if role == "/Formula"
        ] == []
    finally:
        result.close_output_claim()
