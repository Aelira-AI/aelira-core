"""Contract tests for verified chemical-formula PDF recognition."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError


def _occurrence_id() -> str:
    identity = "1|7|0|0|10.000000,20.000000,110.000000,90.000000"
    return "imgocc-v1-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _locator() -> dict[str, object]:
    return {
        "source_kind": "embedded_image_occurrence",
        "page_number": 1,
        "image_xref": 7,
        "image_index": 0,
        "occurrence_ordinal": 0,
        "bbox": [10.0, 20.0, 110.0, 90.0],
        "image_stream_sha256": "1" * 64,
        "occurrence_id": _occurrence_id(),
    }


def _jpeg() -> bytes:
    return b"\xff\xd8bounded-chemical-formula\xff\xd9"


def _request() -> dict[str, object]:
    image = _jpeg()
    return {
        "request_kind": "chemical_formula_recognition_v1",
        "candidate_kind": "chemical_formula",
        "locator": _locator(),
        "mime_type": "image/jpeg",
        "image_bytes": image,
        "normalized_source_sha256": hashlib.sha256(image).hexdigest(),
    }


def _provider_json(source: str = "H2O") -> str:
    return json.dumps(
        {"classification": "chemical_formula", "source_notation": source},
        separators=(",", ":"),
    )


class _Client:
    purpose = "alt_text"
    provider = "gemini"
    model = "formula-test-v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def analyze_image_sync(self, **kwargs):
        self.calls += 1
        assert kwargs["image_data"] == _jpeg()
        assert kwargs["max_tokens"] <= 2048
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _recognize(source: str = "H2O", client=None):
    from src.education.chemical_formula_pdf import (
        ChemicalFormulaRecognitionRequestV1,
        ChemicalFormulaRecognizer,
    )

    request = ChemicalFormulaRecognitionRequestV1.model_validate(_request())
    client = client or _Client([{"success": True, "content": _provider_json(source)}])
    return ChemicalFormulaRecognizer(client).recognize(request)


def test_request_is_frozen_exact_typed_and_digest_bound():
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionRequestV1

    request = ChemicalFormulaRecognitionRequestV1.model_validate(_request())
    with pytest.raises(ValidationError):
        request.mime_type = "image/png"
    for mutation in (
        {"candidate_kind": "printed_equation"},
        {"callback": "active"},
        {"normalized_source_sha256": "0" * 64},
    ):
        with pytest.raises(ValidationError):
            ChemicalFormulaRecognitionRequestV1.model_validate(
                {**_request(), **mutation}
            )


def test_recognizer_uses_225_as_semantic_authority():
    from src.education.chemical_formula import verify_chemical_notation

    recognition = _recognize("2H2(g) + O2(g) -> 2H2O(l)")
    assert recognition.verified_notation == verify_chemical_notation(
        "2H2(g) + O2(g) -> 2H2O(l)"
    )
    assert recognition.provider == "gemini"
    assert recognition.attempts == 1


def test_wrong_purpose_makes_zero_transport_calls():
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionRejected

    client = _Client([{"success": True, "content": _provider_json()}])
    client.purpose = "remediation"
    with pytest.raises(ChemicalFormulaRecognitionRejected, match="purpose_mismatch"):
        _recognize(client=client)
    assert client.calls == 0


@pytest.mark.parametrize("field", ["provider", "model"])
@pytest.mark.parametrize("value", ["", " untrimmed", "x" * 201])
def test_provider_and_model_identity_are_bounded(field, value):
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionRejected

    client = _Client([{"success": True, "content": _provider_json()}])
    setattr(client, field, value)
    with pytest.raises(ChemicalFormulaRecognitionRejected, match="identity_missing"):
        _recognize(client=client)
    assert client.calls == 0


@pytest.mark.parametrize(
    "content",
    [
        "prefix " + _provider_json(),
        _provider_json() + "\n",
        '{"classification":"chemical_formula","classification":"x","source_notation":"H2O"}',
        '{"classification":"chemical_formula","source_notation":"H2O","confidence":1}',
        '{"classification":"not_chemical_formula","source_notation":"H2O"}',
        '{"classification":"chemical_formula","source_notation":"H2O","speech":"water"}',
        "[]",
    ],
)
def test_parser_rejects_prose_duplicates_unknown_fields_and_projections(content):
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionRejected

    with pytest.raises(
        ChemicalFormulaRecognitionRejected, match="invalid_provider_response"
    ):
        _recognize(client=_Client([{"success": True, "content": content}]))


def test_provider_failure_retries_once_but_invalid_notation_does_not():
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionRejected

    client = _Client(
        [RuntimeError("transient"), {"success": True, "content": _provider_json()}]
    )
    assert _recognize(client=client).attempts == 2
    assert client.calls == 2

    client = _Client([{"success": True, "content": _provider_json("water")}])
    with pytest.raises(ChemicalFormulaRecognitionRejected, match="notation_rejected"):
        _recognize(client=client)
    assert client.calls == 1


def test_provider_failure_exhaustion_fails_closed_after_two_calls():
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionRejected

    client = _Client([RuntimeError("one"), RuntimeError("two")])
    with pytest.raises(ChemicalFormulaRecognitionRejected, match="provider_failure"):
        _recognize(client=client)
    assert client.calls == 2


def test_negative_classification_has_no_semantic_output():
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionRejected

    content = '{"classification":"not_chemical_formula","source_notation":null}'
    with pytest.raises(
        ChemicalFormulaRecognitionRejected, match="not_chemical_formula"
    ):
        _recognize(client=_Client([{"success": True, "content": content}]))


@pytest.mark.parametrize(
    "source",
    [
        "H2O",
        "Ca(OH)2",
        "^13CO2",
        "NH4^+",
        "NaCl(aq)",
        "2H2(g) + O2(g) -> 2H2O(l)",
        "N2 + 3H2 <=>[Fe;450 C] 2NH3",
    ],
)
def test_formulas_reactions_charge_isotopes_states_and_conditions(source):
    recognition = _recognize(source)
    assert recognition.verified_notation.source_notation == source
    assert recognition.verified_notation.speech
    assert recognition.verified_notation.mathml.startswith("<math")


@pytest.mark.parametrize(
    "source",
    ["water", "x + y", "H2O trailing", "C6H6 ->", "H2O -> CO2 -> O2", "*"],
)
def test_ambiguous_unsupported_and_false_positive_notation_refuses(source):
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionRejected

    with pytest.raises(ChemicalFormulaRecognitionRejected, match="notation_rejected"):
        _recognize(source)


def test_semantic_output_and_pending_recompute_all_projections():
    from src.education.chemical_formula_pdf import (
        ChemicalFormulaPendingAssociationV1,
        chemical_formula_semantic_output,
    )
    from src.education.visual_semantic_contract import (
        ChemicalFormulaSemanticV1,
        SemanticOutputAdapter,
    )

    recognition = _recognize("NaCl(aq)")
    semantic = chemical_formula_semantic_output("NaCl(aq)")
    restored = SemanticOutputAdapter.validate_python(semantic.model_dump(mode="json"))
    assert isinstance(restored, ChemicalFormulaSemanticV1)
    pending = ChemicalFormulaPendingAssociationV1(
        pending_kind="chemical_formula_pdf_association_v1",
        locator=_locator(),
        semantic_output=semantic,
        recognition=recognition,
    )
    assert pending.alt_text == semantic.verified_notation.speech
    assert pending.mathml_string == semantic.verified_notation.mathml

    value = pending.model_dump(mode="json")
    value["semantic_output"]["verified_notation"]["speech"] = "forged"
    with pytest.raises(ValidationError):
        ChemicalFormulaPendingAssociationV1.model_validate(value)


def _contract() -> dict[str, object]:
    from src.education.chemical_formula_pdf import chemical_formula_semantic_output
    from src.education.visual_semantic_contract import canonical_sha256

    semantic = chemical_formula_semantic_output("H2O").model_dump(mode="json")
    notation = semantic["verified_notation"]
    recognition = {
        "evidence_kind": "chemical_formula_recognition_v1",
        "passed": True,
        "normalized_source_sha256": hashlib.sha256(_jpeg()).hexdigest(),
        "source_sha256": notation["source_sha256"],
        "semantic_sha256": notation["semantic_sha256"],
        "speech_sha256": notation["speech_sha256"],
        "mathml_sha256": notation["mathml_sha256"],
        "provider": "gemini",
        "model": "formula-test-v1",
        "response_sha256": "2" * 64,
        "verifier_version": "chemical-formula-pdf-v1",
        "attempts": 1,
    }
    metadata = {
        "notation_kind": notation["notation"]["notation_kind"],
        "source_sha256": notation["source_sha256"],
        "semantic_sha256": notation["semantic_sha256"],
        "speech_sha256": notation["speech_sha256"],
        "mathml_sha256": notation["mathml_sha256"],
    }
    saved = {
        "evidence_kind": "standalone_chemical_formula_saved_v1",
        "passed": True,
        "saved_file_sha256": "3" * 64,
        "page_number": 1,
        "image_xref": 7,
        "occurrence_ordinal": 0,
        "struct_parent": 4,
        "mcid": 8,
        "source_sha256": notation["source_sha256"],
        "semantic_sha256": notation["semantic_sha256"],
        "speech_sha256": notation["speech_sha256"],
        "mathml_sha256": notation["mathml_sha256"],
        "alt_text_sha256": notation["speech_sha256"],
        "image_stream_sha256": "1" * 64,
        "metadata_sha256": canonical_sha256(metadata),
        "render_signatures": [[144, 200, 140, 600, 7, "5" * 64]],
    }
    specialist = {
        "contract_kind": "chemical_formula",
        "locator": _locator(),
        "semantic_output": semantic,
        "normalized_source_sha256": hashlib.sha256(_jpeg()).hexdigest(),
        "recognition_evidence": recognition,
    }
    value = {
        **{k: v for k, v in specialist.items() if k != "recognition_evidence"},
        "verification_evidence": [recognition, saved],
        "specialist_sha256": canonical_sha256(specialist),
    }
    value["contract_sha256"] = canonical_sha256(value)
    return value


def test_complete_contract_is_public_and_durable():
    from src.education.visual_semantic_contract import (
        ChemicalFormulaPdfContract,
        VisualSemanticContractAdapter,
    )

    contract = VisualSemanticContractAdapter.validate_python(_contract())
    assert isinstance(contract, ChemicalFormulaPdfContract)
    restored = VisualSemanticContractAdapter.validate_json(contract.model_dump_json())
    assert restored == contract


@pytest.mark.parametrize(
    ("evidence_index", "field"),
    [
        (0, "source_sha256"),
        (0, "semantic_sha256"),
        (0, "speech_sha256"),
        (0, "mathml_sha256"),
        (1, "source_sha256"),
        (1, "semantic_sha256"),
        (1, "speech_sha256"),
        (1, "mathml_sha256"),
        (1, "metadata_sha256"),
    ],
)
def test_complete_contract_rejects_every_semantic_tamper(evidence_index, field):
    from src.education.visual_semantic_contract import VisualSemanticContractAdapter

    value = deepcopy(_contract())
    value["verification_evidence"][evidence_index][field] = "0" * 64
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python(value)
