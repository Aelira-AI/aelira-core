"""Purpose-bound HMER recognition and handwriting-specific verification."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.education.handwritten_math_suitability import (
    POLICY_SHA256,
    HandwrittenMathSuitabilityEvidence,
    classify_handwritten_math_suitability,
    load_corpus_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = ROOT / "tests" / "fixtures" / "handwritten_math"
DOC_PATH = ROOT / "docs" / "document-remediation" / "handwritten-math.md"


class Client:
    purpose = "alt_text"
    provider = "fixture-provider"
    model = "fixture-hmer-v1"

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def analyze_image_sync(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _success(content: str) -> dict[str, object]:
    return {"success": True, "content": content}


def _jpeg(fixture_id: str) -> bytes:
    manifest = load_corpus_manifest(CORPUS_ROOT / "manifest.json")
    fixture = next(item for item in manifest.fixtures if item.id == fixture_id)
    with Image.open(CORPUS_ROOT / fixture.path) as image:
        output = io.BytesIO()
        image.convert("RGB").save(
            output,
            format="JPEG",
            quality=95,
            subsampling=0,
            optimize=False,
            progressive=False,
        )
    return output.getvalue()


def _raster(fixture_id: str = "legible-linear") -> SimpleNamespace:
    payload = _jpeg(fixture_id)
    with Image.open(io.BytesIO(payload)) as image:
        width, height = image.size
    return SimpleNamespace(
        jpeg_bytes=payload,
        mime_type="image/jpeg",
        normalized_sha256=hashlib.sha256(payload).hexdigest(),
        width=width,
        height=height,
    )


def _evidence(
    raster: SimpleNamespace,
) -> HandwrittenMathSuitabilityEvidence:
    return classify_handwritten_math_suitability(raster.jpeg_bytes)


def test_primary_hmer_recognizer_requires_exact_eligibility_and_alt_text_purpose():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )

    raster = _raster()
    evidence = _evidence(raster)
    client = Client(
        _success('{"classification":"handwritten_equation","latex":"x^2 + y = 10"}')
    )
    result = HandwrittenEquationRecognizer(client).recognize(raster, evidence)

    assert result.classification == "handwritten_equation"
    assert result.latex == "x^2 + y = 10"
    assert result.source_sha256 == raster.normalized_sha256
    assert result.suitability_evidence_sha256 == evidence.evidence_sha256
    assert result.provider == "fixture-provider"
    assert result.model == "fixture-hmer-v1"
    assert len(result.response_sha256) == 64
    assert len(client.calls) == 1
    assert client.calls[0]["image_data"] == raster.jpeg_bytes
    assert "handwritten" in str(client.calls[0]["prompt"]).lower()

    denied = Client(_success('{"classification":"not_handwritten_math","latex":null}'))
    denied.purpose = "remediation"
    with pytest.raises(ValueError, match="purpose_mismatch"):
        HandwrittenEquationRecognizer(denied).recognize(raster, evidence)
    assert denied.calls == []


def test_noneligible_or_forged_suitability_never_reaches_hmer_transport():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognitionRejected,
        HandwrittenEquationRecognizer,
    )

    held = _raster("low-contrast")
    held_client = Client(
        _success('{"classification":"handwritten_equation","latex":"x"}')
    )
    with pytest.raises(HandwrittenEquationRecognitionRejected, match="not_eligible"):
        HandwrittenEquationRecognizer(held_client).recognize(held, _evidence(held))
    assert held_client.calls == []

    eligible = _raster()
    forged = _evidence(eligible).model_dump(mode="json")
    forged["source_sha256"] = "0" * 64
    forged_client = Client(
        _success('{"classification":"handwritten_equation","latex":"x"}')
    )
    with pytest.raises(
        HandwrittenEquationRecognitionRejected, match="evidence_invalid"
    ):
        HandwrittenEquationRecognizer(forged_client).recognize(eligible, forged)
    assert forged_client.calls == []


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"classification":"handwritten_equation","latex":"x"}\n```',
        '{"classification":"handwritten_equation","latex":"x","extra":1}',
        '{"classification":"handwritten_equation","latex":"x","latex":"y"}',
        '{"classification":"handwritten_equation","latex":null}',
        '{"classification":"not_handwritten_math","latex":"x"}',
        '{"classification":"unsupported_notation","latex":"x"}',
        '{"classification":"unknown","latex":null}',
        '[{"classification":"handwritten_equation","latex":"x"}]',
        '{"classification":"handwritten_equation","latex":"x\ny"}',
    ],
)
def test_primary_hmer_response_contract_fails_closed(content: str):
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognitionRejected,
        HandwrittenEquationRecognizer,
    )

    raster = _raster()
    with pytest.raises(
        HandwrittenEquationRecognitionRejected, match="invalid_provider_response"
    ):
        HandwrittenEquationRecognizer(Client(_success(content))).recognize(
            raster, _evidence(raster)
        )


@pytest.mark.parametrize(
    "classification", ["not_handwritten_math", "unsupported_notation"]
)
def test_nonsemantic_hmer_classifications_carry_no_latex(classification: str):
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )

    raster = _raster()
    result = HandwrittenEquationRecognizer(
        Client(_success(json.dumps({"classification": classification, "latex": None})))
    ).recognize(raster, _evidence(raster))

    assert result.classification == classification
    assert result.latex is None
    assert result.latex_sha256 is None


def test_verifier_uses_fresh_context_and_accepts_exact_canonical_agreement():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.handwritten_equation_policy import (
        HANDWRITTEN_VERIFIER_POLICY_SHA256,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerifier,
    )

    raster = _raster()
    suitability = _evidence(raster)
    primary_client = Client(
        _success('{"classification":"handwritten_equation","latex":"x^2+y=10"}')
    )
    primary = HandwrittenEquationRecognizer(primary_client).recognize(
        raster, suitability
    )
    verifier_client = Client(
        _success('{"classification":"handwritten_equation","latex":"x^2 + y = 10"}')
    )
    evidence = HandwrittenEquationVerifier(verifier_client).verify(
        raster, suitability, primary
    )

    assert evidence.passed is True
    assert evidence.source_sha256 == raster.normalized_sha256
    assert evidence.suitability_evidence_sha256 == suitability.evidence_sha256
    assert evidence.suitability_policy_sha256 == POLICY_SHA256
    assert evidence.verifier_policy_sha256 == HANDWRITTEN_VERIFIER_POLICY_SHA256
    assert (
        HANDWRITTEN_VERIFIER_POLICY_SHA256
        == "33e2ae7168e9d3b345088b375edffdde12ffa0f69ba8e9a445447aea589dbe81"
    )
    assert evidence.agreement_count == evidence.required_agreement_count == 2
    assert evidence.primary_mathml_sha256 == evidence.verifier_mathml_sha256
    assert evidence.mathml_sha256 == evidence.primary_mathml_sha256
    assert len(verifier_client.calls) == 1
    verifier_prompt = str(verifier_client.calls[0]["prompt"])
    assert primary.latex not in verifier_prompt
    assert primary.math_candidate_sha256 not in verifier_prompt


def test_verifier_rejects_disagreement_and_provider_failure_without_payload_leak():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerificationRejected,
        HandwrittenEquationVerifier,
    )

    raster = _raster()
    suitability = _evidence(raster)
    primary = HandwrittenEquationRecognizer(
        Client(_success('{"classification":"handwritten_equation","latex":"x^2+y=10"}'))
    ).recognize(raster, suitability)

    mismatch = Client(
        _success('{"classification":"handwritten_equation","latex":"x^2+y=11"}')
    )
    with pytest.raises(
        HandwrittenEquationVerificationRejected, match="semantic_disagreement"
    ):
        HandwrittenEquationVerifier(mismatch).verify(raster, suitability, primary)

    failed = Client(RuntimeError("secret provider payload"))
    with pytest.raises(
        HandwrittenEquationVerificationRejected, match="provider_failure"
    ) as exc:
        HandwrittenEquationVerifier(failed).verify(raster, suitability, primary)
    assert exc.value.__cause__ is None
    assert "secret provider payload" not in str(exc.value)


def test_verifier_rejects_tampered_primary_and_an_independent_decline():
    from dataclasses import replace

    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerificationRejected,
        HandwrittenEquationVerifier,
    )

    raster = _raster()
    suitability = _evidence(raster)
    primary = HandwrittenEquationRecognizer(
        Client(_success('{"classification":"handwritten_equation","latex":"x"}'))
    ).recognize(raster, suitability)

    with pytest.raises(
        HandwrittenEquationVerificationRejected, match="primary_evidence_mismatch"
    ):
        HandwrittenEquationVerifier(Client()).verify(
            raster, suitability, replace(primary, source_sha256="0" * 64)
        )

    declined = Client(
        _success('{"classification":"not_handwritten_math","latex":null}')
    )
    with pytest.raises(
        HandwrittenEquationVerificationRejected, match="verifier_declined"
    ):
        HandwrittenEquationVerifier(declined).verify(raster, suitability, primary)


def test_frozen_corpus_calibrates_hmer_admission_without_calls_for_held_cases():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognitionRejected,
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerifier,
    )

    manifest = load_corpus_manifest(CORPUS_ROOT / "manifest.json")
    for fixture in manifest.fixtures:
        raster = _raster(fixture.id)
        suitability = _evidence(raster)
        assert suitability.disposition == fixture.expected_disposition
        response = _success(
            '{"classification":"handwritten_equation","latex":"x^2+y=10"}'
        )
        client = Client(response, response)
        if fixture.expected_disposition == "eligible":
            result = HandwrittenEquationRecognizer(client).recognize(
                raster, suitability
            )
            assert result.classification == "handwritten_equation"
            consensus = HandwrittenEquationVerifier(client).verify(
                raster, suitability, result
            )
            assert consensus.passed is True
            assert consensus.agreement_count == 2
            assert len(client.calls) == 2
        else:
            with pytest.raises(
                HandwrittenEquationRecognitionRejected, match="not_eligible"
            ):
                HandwrittenEquationRecognizer(client).recognize(raster, suitability)
            assert client.calls == []


def test_hmer_documentation_names_every_trust_boundary_and_probe():
    text = " ".join(DOC_PATH.read_text().split())
    for required in (
        "Printed-equation decline",
        "Exact suitability admission",
        "Primary HMER reading",
        "Independent verifier reading",
        "Saved-file association",
        "Human approval",
        "exact canonical MathML agreement",
        "python scripts/evaluate_handwritten_math_corpus.py --subset full",
        "low contrast",
        "strike-through",
        "annotations",
        "multiple lines",
        "unsupported notation",
        "non-math handwriting",
    ):
        assert required in text


def _verify_pair(primary_latex: str, verifier_latex: str, **options):
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerifier,
    )

    raster = _raster()
    suitability = _evidence(raster)

    def reading(latex: str) -> str:
        return _success(
            '{"classification":"handwritten_equation","latex":"'
            + latex.replace("\\", "\\\\")
            + '"}'
        )

    primary = HandwrittenEquationRecognizer(Client(reading(primary_latex))).recognize(
        raster, suitability
    )
    return HandwrittenEquationVerifier(
        Client(reading(verifier_latex)), **options
    ).verify(raster, suitability, primary)


def test_verifier_reading_refusal_runs_only_after_the_readings_agree():
    from src.education.remediation.handwritten_equation_verifier import (
        ALT_TEXT_AMBIGUOUS,
        HandwrittenEquationVerificationRejected,
    )

    seen: list[str] = []

    def refuse(latex: str) -> bool:
        seen.append(latex)
        return "frac12" in latex

    # Without the check, the agreed pair passes as before.
    assert _verify_pair(r"\frac{1}{2}+y=4", r"\frac12+y=4").passed is True

    with pytest.raises(HandwrittenEquationVerificationRejected) as agreed:
        _verify_pair(r"\frac{1}{2}+y=4", r"\frac12+y=4", reading_refusal=refuse)
    assert str(agreed.value) == ALT_TEXT_AMBIGUOUS
    assert seen == [r"\frac12+y=4"]

    # A clean verifier reading passes the same check.
    assert (
        _verify_pair(
            r"\frac{1}{2}+y=4", r"\frac{1}{2}+y=4", reading_refusal=refuse
        ).passed
        is True
    )

    # A disagreement is reported as a disagreement and never reaches the check.
    seen.clear()
    with pytest.raises(
        HandwrittenEquationVerificationRejected, match="semantic_disagreement"
    ):
        _verify_pair(r"\frac{1}{2}+y=4", r"\frac12+y=5", reading_refusal=refuse)
    assert seen == []


# ---------------------------------------------------------------------------
# The reading is not cut off, and a replayed answer names no model
# ---------------------------------------------------------------------------

REPLAYED = [
    {"model": "cached"},
    {"model": " CACHED "},
    {"metadata": {"cached": True}},
    {"cached": True},
]


def _hmer(latex: str = "x^2 + y = 10") -> str:
    return '{"classification":"handwritten_equation","latex":"' + latex + '"}'


def test_both_handwritten_readers_ask_for_enough_tokens_to_finish_the_longest_reading():
    from src.education.remediation.equation_reading_parser import reading_max_tokens
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerifier,
    )

    raster = _raster()
    suitability = _evidence(raster)
    wanted = reading_max_tokens(4096)
    assert wanted > 500, "the old allowance of 500 tokens cut long readings off"
    primary_client = Client(_success(_hmer()))
    primary = HandwrittenEquationRecognizer(primary_client).recognize(
        raster, suitability
    )
    verifier_client = Client(_success(_hmer()))
    HandwrittenEquationVerifier(verifier_client).verify(raster, suitability, primary)

    assert primary_client.calls[0]["max_tokens"] == wanted
    assert verifier_client.calls[0]["max_tokens"] == wanted
    short_client = Client(_success(_hmer()))
    HandwrittenEquationRecognizer(short_client, max_latex_chars=200).recognize(
        raster, suitability
    )
    assert short_client.calls[0]["max_tokens"] == reading_max_tokens(200) == 500


@pytest.mark.parametrize("replayed", REPLAYED)
def test_a_replayed_primary_reading_names_no_model_and_is_refused(replayed):
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )

    raster = _raster()
    client = Client({**_success(_hmer()), **replayed})

    with pytest.raises(ValueError, match="^provider_identity_unavailable$"):
        HandwrittenEquationRecognizer(client).recognize(raster, _evidence(raster))


@pytest.mark.parametrize("replayed", REPLAYED)
def test_a_replayed_verifier_reading_names_no_model_and_is_refused(replayed):
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerifier,
    )

    raster = _raster()
    suitability = _evidence(raster)
    primary = HandwrittenEquationRecognizer(Client(_success(_hmer()))).recognize(
        raster, suitability
    )
    verifier = HandwrittenEquationVerifier(Client({**_success(_hmer()), **replayed}))

    with pytest.raises(ValueError, match="^provider_identity_unavailable$"):
        verifier.verify(raster, suitability, primary)


def test_a_client_that_declares_the_model_cached_has_no_model_either():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )

    class CachedClient(Client):
        model = "cached"

    raster = _raster()
    with pytest.raises(ValueError, match="^provider_identity_unavailable$"):
        HandwrittenEquationRecognizer(CachedClient(_success(_hmer()))).recognize(
            raster, _evidence(raster)
        )


def test_a_fresh_handwritten_answer_that_names_its_model_is_still_recorded():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )

    raster = _raster()
    answer = {**_success(_hmer()), "model": "hmer-1"}

    recognition = HandwrittenEquationRecognizer(Client(answer)).recognize(
        raster, _evidence(raster)
    )

    assert recognition.model == "hmer-1"
