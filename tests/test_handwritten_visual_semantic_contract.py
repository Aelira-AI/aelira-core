"""Typed visual-semantic contract coverage for handwritten equations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

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


V1_POLICY_VERSION = "handwritten-equation-consensus-v1"


def _v1_policy_sha256() -> str:
    """The v1 digest: no canonical-form version, so it predates MathML v2."""
    return _sha256(
        {
            "policy_version": V1_POLICY_VERSION,
            "agreement": "exact_canonical_mathml",
            "required_agreement_count": 2,
            "suitability_policy_sha256": POLICY_SHA256,
        }
    )


def _v1_consensus() -> dict[str, object]:
    return _consensus(
        verifier_policy_version=V1_POLICY_VERSION,
        verifier_policy_sha256=_v1_policy_sha256(),
    )


def _stored_row(contract: dict[str, object], consensus: dict[str, object]):
    return SimpleNamespace(
        source_kind="image_equation",
        fixed_content=_semantic()["alt_text"],
        page_number=2,
        verification_evidence={
            key: value for key, value in consensus.items() if key != "evidence_kind"
        },
        visual_semantic_contract=contract,
        source_locator=None,
    )


def test_current_policy_evidence_is_the_positive_control_for_v1_rejection():
    from src.education.visual_semantic_contract import (
        HandwrittenEquationConsensusEvidenceV1,
    )

    HandwrittenEquationConsensusEvidenceV1.model_validate(_consensus())
    assert V1_POLICY_VERSION != HANDWRITTEN_VERIFIER_POLICY_VERSION
    assert _v1_policy_sha256() != HANDWRITTEN_VERIFIER_POLICY_SHA256


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {
                "verifier_policy_version": V1_POLICY_VERSION,
                "verifier_policy_sha256": _v1_policy_sha256(),
            },
            id="v1-version-and-digest",
        ),
        pytest.param(
            {"verifier_policy_version": V1_POLICY_VERSION},
            id="v1-version-current-digest",
        ),
        pytest.param(
            {"verifier_policy_sha256": _v1_policy_sha256()},
            id="current-version-v1-digest",
        ),
    ],
)
def test_handwritten_evidence_recorded_under_policy_v1_fails_closed(overrides):
    from src.education.remediation.base import HandwrittenVerificationEvidence
    from src.education.visual_semantic_contract import (
        HandwrittenEquationConsensusEvidenceV1,
        HandwrittenEquationContract,
        VisualSemanticContractAdapter,
    )

    consensus = _consensus(**overrides)
    legacy = {k: v for k, v in consensus.items() if k != "evidence_kind"}
    saved = _standalone_saved()
    raw = _contract(_embedded_locator(), saved, evidence=[consensus, saved])

    with pytest.raises(ValidationError):
        HandwrittenEquationConsensusEvidenceV1.model_validate(consensus)
    with pytest.raises(ValidationError):
        HandwrittenVerificationEvidence.model_validate(legacy)
    with pytest.raises(ValidationError):
        HandwrittenEquationContract.model_validate(raw)
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python(raw)


def test_stored_policy_v1_handwritten_row_is_invalid_not_a_crash():
    from src.services.scan_fix_service import (
        image_equation_review_blockers,
        validated_visual_semantic_contract,
        visual_semantic_disposition,
        valid_image_equation_evidence,
    )

    saved = _standalone_saved()
    current = _stored_row(
        _contract(_embedded_locator(), saved, evidence=[_consensus(), saved]),
        _consensus(),
    )
    legacy_consensus = _v1_consensus()
    legacy = _stored_row(
        _contract(_embedded_locator(), saved, evidence=[legacy_consensus, saved]),
        legacy_consensus,
    )

    assert visual_semantic_disposition(current) == "complete"
    assert valid_image_equation_evidence(current.verification_evidence) is True

    assert validated_visual_semantic_contract(legacy.visual_semantic_contract) is None
    assert valid_image_equation_evidence(legacy.verification_evidence) is False
    assert visual_semantic_disposition(legacy) == "invalid"
    blockers = image_equation_review_blockers([legacy])
    assert "image_equation_visual_contract_invalid" in blockers
    assert "image_equation_evidence_invalid" in blockers


@pytest.mark.parametrize(
    "overrides",
    [
        # A replayed provider answer is labelled "cached", which names no model.
        {"primary_model": "cached"},
        {"verifier_model": "Cached"},
        {"primary_model": "CACHED"},
    ],
)
def test_handwritten_consensus_evidence_refuses_a_cached_model(overrides):
    from pydantic import ValidationError

    from src.education.visual_semantic_contract import (
        HandwrittenEquationConsensusEvidenceV1,
    )

    assert HandwrittenEquationConsensusEvidenceV1.model_validate(_consensus())
    with pytest.raises(ValidationError):
        HandwrittenEquationConsensusEvidenceV1.model_validate(_consensus(**overrides))
