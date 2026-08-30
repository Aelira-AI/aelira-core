"""Typed visual-semantic contract coverage for handwritten equations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.education.handwritten_math_suitability import (
    POLICY_SHA256,
    classify_handwritten_math_suitability,
)
from src.education.handwritten_equation_policy import (
    HANDWRITTEN_VERIFIER_POLICY_SHA256,
    HANDWRITTEN_VERIFIER_POLICY_VERSION,
)
from tests.test_visual_semantic_contract import (
    HASHES,
    _embedded_locator,
    _page_locator,
    _scanned_saved,
    _semantic,
    _standalone_saved,
)

ROOT = Path(__file__).resolve().parents[1]


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _suitability() -> dict[str, object]:
    payload = (
        ROOT
        / "tests"
        / "fixtures"
        / "handwritten_math"
        / "images"
        / "legible-linear.png"
    ).read_bytes()
    return classify_handwritten_math_suitability(payload).model_dump(mode="json")


def _consensus(**overrides: object) -> dict[str, object]:
    suitability = _suitability()
    value: dict[str, object] = {
        "evidence_kind": "handwritten_equation_consensus_v1",
        "passed": True,
        "source_sha256": suitability["source_sha256"],
        "mathml_sha256": _semantic()["mathml_sha256"],
        "suitability_evidence": suitability,
        "suitability_evidence_sha256": suitability["evidence_sha256"],
        "suitability_policy_sha256": POLICY_SHA256,
        "verifier_policy_version": HANDWRITTEN_VERIFIER_POLICY_VERSION,
        "verifier_policy_sha256": HANDWRITTEN_VERIFIER_POLICY_SHA256,
        "agreement_count": 2,
        "required_agreement_count": 2,
        "primary_mathml_sha256": _semantic()["mathml_sha256"],
        "verifier_mathml_sha256": _semantic()["mathml_sha256"],
        "primary_response_sha256": HASHES[0],
        "verifier_response_sha256": HASHES[1],
        "primary_latex_sha256": HASHES[2],
        "verifier_latex_sha256": HASHES[3],
        "primary_provider": "fixture-primary",
        "primary_model": "hmer-primary-v1",
        "verifier_provider": "fixture-verifier",
        "verifier_model": "hmer-verifier-v1",
    }
    value.update(overrides)
    return value


def _contract(
    locator: dict[str, object],
    saved: dict[str, object],
    *,
    evidence: list[dict[str, object]] | None = None,
    semantic: dict[str, object] | None = None,
) -> dict[str, object]:
    output = semantic or _semantic()
    proofs = evidence or [_consensus(), saved]
    specialist = {
        "contract_kind": "handwritten_equation",
        "locator": locator,
        "semantic_output": output,
        "normalized_source_sha256": _suitability()["source_sha256"],
    }
    value: dict[str, object] = {
        **specialist,
        "verification_evidence": proofs,
        "specialist_sha256": _sha256(specialist),
    }
    value["contract_sha256"] = _sha256(value)
    return value


@pytest.mark.parametrize(
    ("locator", "saved"),
    [
        (_embedded_locator(), _standalone_saved()),
        (_page_locator(), _scanned_saved()),
    ],
)
def test_handwritten_contract_accepts_exact_consensus_and_saved_pair(
    locator: dict[str, object], saved: dict[str, object]
):
    from src.education.visual_semantic_contract import HandwrittenEquationContract

    raw = _contract(locator, saved)
    contract = HandwrittenEquationContract.model_validate(raw)

    assert contract.model_dump(mode="json") == raw
    assert contract.model_config["extra"] == "forbid"
    assert contract.model_config["frozen"] is True


def test_visual_contract_adapter_activates_both_exact_specialists():
    from src.education.visual_semantic_contract import VisualSemanticContractAdapter

    assert VisualSemanticContractAdapter.json_schema()["discriminator"] == {
        "mapping": {
            "chemical_structure": "#/$defs/ChemicalStructurePdfContract",
            "chemical_formula": "#/$defs/ChemicalFormulaPdfContract",
            "commutative_diagram": "#/$defs/CommutativeDiagramPdfContract",
            "handwritten_equation": "#/$defs/HandwrittenEquationContract",
            "printed_equation": "#/$defs/PrintedEquationContract",
        },
        "propertyName": "contract_kind",
    }
    parsed = VisualSemanticContractAdapter.validate_python(
        _contract(_embedded_locator(), _standalone_saved())
    )
    assert parsed.contract_kind == "handwritten_equation"


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_source",
        "wrong_mathml",
        "wrong_policy",
        "wrong_count",
        "printed_evidence",
        "duplicate_consensus",
        "cross_saved",
        "specialist_digest",
        "contract_digest",
        "unknown_field",
    ],
)
def test_handwritten_contract_rejects_cross_source_cross_variant_and_tampering(
    mutation: str,
):
    from src.education.visual_semantic_contract import HandwrittenEquationContract
    from tests.test_visual_semantic_contract import _roundtrip

    locator = _embedded_locator()
    saved = _standalone_saved()
    evidence = [_consensus(), saved]
    raw = _contract(locator, saved, evidence=evidence)
    if mutation == "wrong_source":
        raw = _contract(
            locator,
            saved,
            evidence=[_consensus(source_sha256=HASHES[4]), saved],
        )
    elif mutation == "wrong_mathml":
        raw = _contract(
            locator,
            saved,
            evidence=[_consensus(mathml_sha256=HASHES[4]), saved],
        )
    elif mutation == "wrong_policy":
        raw = _contract(
            locator,
            saved,
            evidence=[_consensus(verifier_policy_sha256=HASHES[4]), saved],
        )
    elif mutation == "wrong_count":
        raw = _contract(
            locator,
            saved,
            evidence=[_consensus(agreement_count=1), saved],
        )
    elif mutation == "printed_evidence":
        raw = _contract(locator, saved, evidence=[_roundtrip(), saved])
    elif mutation == "duplicate_consensus":
        raw = _contract(locator, saved, evidence=[_consensus(), _consensus()])
    elif mutation == "cross_saved":
        raw = _contract(locator, _scanned_saved())
    elif mutation == "specialist_digest":
        raw["specialist_sha256"] = HASHES[4]
    elif mutation == "contract_digest":
        raw["contract_sha256"] = HASHES[4]
    elif mutation == "unknown_field":
        raw["provider_payload"] = {"secret": True}

    with pytest.raises(ValidationError):
        HandwrittenEquationContract.model_validate(raw)
