"""Contract tests for verified chemical-structure PDF recognition."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError


def _methyl_graph() -> dict[str, object]:
    return {
        "contract_kind": "molecular_graph_v1",
        "atoms": [
            {
                "atom_id": "outside",
                "element": "O",
                "formal_charge": 0,
                "isotope": None,
                "implicit_hydrogens": 1,
                "aromatic": False,
                "stereochemistry": None,
            },
            {
                "atom_id": "methyl",
                "element": "C",
                "formal_charge": 0,
                "isotope": None,
                "implicit_hydrogens": 3,
                "aromatic": False,
                "stereochemistry": None,
            },
        ],
        "bonds": [
            {
                "bond_id": "bond",
                "atom_ids": ["outside", "methyl"],
                "order": "single",
                "stereochemistry": None,
            }
        ],
    }


def _abbreviation() -> dict[str, object]:
    return {
        "source_token": "Me",
        "anchor_atom_id": "methyl",
        "atom_ids": ["methyl"],
    }


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
    return b"\xff\xd8bounded-chemical-structure\xff\xd9"


def _request() -> dict[str, object]:
    image = _jpeg()
    return {
        "request_kind": "chemical_structure_recognition_v1",
        "locator": _locator(),
        "mime_type": "image/jpeg",
        "image_bytes": image,
        "normalized_source_sha256": hashlib.sha256(image).hexdigest(),
    }


def _provider_json(
    *,
    graph: dict[str, object] | None = None,
    abbreviations: list[dict[str, object]] | None = None,
) -> str:
    return json.dumps(
        {
            "classification": "chemical_structure",
            "graph": graph if graph is not None else _methyl_graph(),
            "abbreviations": (
                abbreviations if abbreviations is not None else [_abbreviation()]
            ),
        },
        separators=(",", ":"),
    )


class _Client:
    purpose = "alt_text"
    provider = "gemini"
    model = "structure-test-v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def analyze_image_sync(self, **kwargs):
        self.calls += 1
        assert kwargs["image_data"] == _jpeg()
        assert kwargs["max_tokens"] <= 4096
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _recognize(client=None):
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRequestV1,
        ChemicalStructureRecognizer,
    )

    request = ChemicalStructureRecognitionRequestV1.model_validate(_request())
    client = client or _Client([{"success": True, "content": _provider_json()}])
    return ChemicalStructureRecognizer(client).recognize(request)


def test_request_is_frozen_exact_and_digest_bound():
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRequestV1,
    )

    request = ChemicalStructureRecognitionRequestV1.model_validate(_request())
    with pytest.raises(ValidationError):
        request.mime_type = "image/png"
    with pytest.raises(ValidationError):
        ChemicalStructureRecognitionRequestV1.model_validate(
            {**_request(), "callback": "active"}
        )
    with pytest.raises(ValidationError, match="normalized_source_sha256"):
        ChemicalStructureRecognitionRequestV1.model_validate(
            {**_request(), "normalized_source_sha256": "0" * 64}
        )


def test_recognizer_returns_verified_expanded_graph_and_abbreviation_evidence():
    recognition = _recognize()

    assert recognition.recognition_kind == "chemical_structure_recognition_v1"
    assert recognition.graph.canonical_sha256 == recognition.graph_sha256
    assert recognition.abbreviations[0].source_token == "Me"
    assert recognition.abbreviation_policy_version == "chemical-abbreviation-v1"
    assert recognition.provider == "gemini"
    assert recognition.attempts == 1


def test_recognizer_rejects_wrong_client_purpose_before_transport():
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRejected,
    )

    client = _Client([{"success": True, "content": _provider_json()}])
    client.purpose = "remediation"
    with pytest.raises(ChemicalStructureRecognitionRejected, match="purpose_mismatch"):
        _recognize(client)
    assert client.calls == 0


@pytest.mark.parametrize("field", ["provider", "model"])
def test_recognizer_requires_bounded_provider_identity(field):
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRejected,
    )

    client = _Client([{"success": True, "content": _provider_json()}])
    setattr(client, field, "")
    with pytest.raises(ChemicalStructureRecognitionRejected, match="identity_missing"):
        _recognize(client)
    assert client.calls == 0


@pytest.mark.parametrize("case", ["ring", "stereo", "charge"])
def test_recognizer_preserves_supported_ring_stereo_and_charge_semantics(case):
    from tests.test_molecular_graph import benzene, chiral_carbon

    if case == "ring":
        graph = benzene()
    elif case == "stereo":
        graph = chiral_carbon("S")
    else:
        graph = _methyl_graph()
        graph["atoms"][0]["formal_charge"] = -1
    recognition = _recognize(
        _Client(
            [
                {
                    "success": True,
                    "content": _provider_json(graph=graph, abbreviations=[]),
                }
            ]
        )
    )
    assert recognition.abbreviations == ()
    if case == "ring":
        assert recognition.graph.topology.cycle_rank == 1
    elif case == "stereo":
        assert any(atom.stereochemistry == "S" for atom in recognition.graph.atoms)
    else:
        assert any(atom.formal_charge == -1 for atom in recognition.graph.atoms)


@pytest.mark.parametrize(
    "content",
    [
        "prefix " + _provider_json(),
        _provider_json() + "\n",
        '{"classification":"chemical_structure","classification":"x","graph":{},"abbreviations":[]}',
        '{"classification":"chemical_structure","graph":{},"abbreviations":[],"confidence":1}',
        '{"classification":"not_chemical_structure","graph":{},"abbreviations":[]}',
        "[]",
    ],
)
def test_recognizer_rejects_prose_duplicates_and_unknown_fields(content):
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRejected,
    )

    with pytest.raises(
        ChemicalStructureRecognitionRejected, match="invalid_provider_response"
    ):
        _recognize(_Client([{"success": True, "content": content}]))


@pytest.mark.parametrize("token", ["Ac", "Ts", "Bn", "Bz", "R", "*"])
def test_recognizer_rejects_unsupported_abbreviations(token):
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRejected,
    )

    evidence = _abbreviation()
    evidence["source_token"] = token
    with pytest.raises(ChemicalStructureRecognitionRejected, match="graph_rejected"):
        _recognize(
            _Client(
                [
                    {
                        "success": True,
                        "content": _provider_json(abbreviations=[evidence]),
                    }
                ]
            )
        )


def test_recognizer_retries_provider_failure_once_but_not_invalid_semantics():
    client = _Client(
        [RuntimeError("transient"), {"success": True, "content": _provider_json()}]
    )
    assert _recognize(client).attempts == 2
    assert client.calls == 2

    invalid = deepcopy(_methyl_graph())
    invalid["atoms"][1]["element"] = "Me"
    client = _Client([{"success": True, "content": _provider_json(graph=invalid)}])
    with pytest.raises(Exception, match="graph_rejected"):
        _recognize(client)
    assert client.calls == 1


def test_provider_failure_exhaustion_fails_closed_after_two_calls():
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRejected,
    )

    client = _Client([RuntimeError("one"), RuntimeError("two")])
    with pytest.raises(ChemicalStructureRecognitionRejected, match="provider_failure"):
        _recognize(client)
    assert client.calls == 2


def test_recognition_digest_binds_exact_provider_response():
    first = _recognize(
        _Client([{"success": True, "content": _provider_json(abbreviations=[])}])
    )
    changed_graph = _methyl_graph()
    changed_graph["atoms"][0]["formal_charge"] = -1
    second = _recognize(
        _Client(
            [
                {
                    "success": True,
                    "content": _provider_json(graph=changed_graph, abbreviations=[]),
                }
            ]
        )
    )
    assert first.response_sha256 != second.response_sha256


@pytest.mark.parametrize("case", ["disconnected", "ambiguous_stereo", "polymer"])
def test_recognizer_fails_closed_on_unsupported_structure_semantics(case):
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRejected,
    )
    from tests.test_molecular_graph import atom, chiral_carbon, graph

    if case == "disconnected":
        value = graph([atom("a", "C"), atom("b", "O")], [])
    elif case == "ambiguous_stereo":
        value = chiral_carbon("R")
        value["atoms"][2]["element"] = "F"
    else:
        value = _methyl_graph()
        value["repeating_unit"] = {"bond_ids": ["bond"]}
    content = _provider_json(graph=value, abbreviations=[])
    with pytest.raises(ChemicalStructureRecognitionRejected, match="graph_rejected"):
        _recognize(_Client([{"success": True, "content": content}]))


def test_not_structure_classification_fails_closed():
    from src.education.chemical_structure_pdf import (
        ChemicalStructureRecognitionRejected,
    )

    content = (
        '{"classification":"not_chemical_structure","graph":null,' '"abbreviations":[]}'
    )
    with pytest.raises(
        ChemicalStructureRecognitionRejected, match="not_chemical_structure"
    ):
        _recognize(_Client([{"success": True, "content": content}]))


def test_semantic_output_derives_description_from_the_graph():
    from src.education.chemical_structure_pdf import chemical_structure_semantic_output
    from src.education.visual_semantic_contract import (
        ChemicalStructureSemanticV1,
        SemanticOutputAdapter,
    )

    semantic = chemical_structure_semantic_output(_recognize().graph)
    restored = SemanticOutputAdapter.validate_python(semantic.model_dump(mode="json"))
    assert isinstance(restored, ChemicalStructureSemanticV1)
    assert restored.description.graph_sha256 == restored.graph_sha256


def test_pending_association_binds_recognition_and_accessible_outputs():
    from src.education.chemical_structure_pdf import (
        ChemicalStructurePendingAssociationV1,
        chemical_structure_semantic_output,
    )

    recognition = _recognize()
    pending = ChemicalStructurePendingAssociationV1(
        pending_kind="chemical_structure_pdf_association_v1",
        locator=_locator(),
        semantic_output=chemical_structure_semantic_output(recognition.graph),
        recognition=recognition,
    )
    assert pending.alt_text == pending.semantic_output.description.summary
    assert pending.graph_attachment_bytes.startswith(b'{"atoms"')
    assert "Topology:" in pending.accessible_text

    value = pending.model_dump(mode="json")
    value["semantic_output"]["graph_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        ChemicalStructurePendingAssociationV1.model_validate(value)


def _semantic() -> dict[str, object]:
    from src.education.chemical_structure_pdf import chemical_structure_semantic_output

    return chemical_structure_semantic_output(_recognize().graph).model_dump(
        mode="json"
    )


def _recognition_evidence(semantic: dict[str, object]) -> dict[str, object]:
    from src.education.visual_semantic_contract import canonical_sha256

    return {
        "evidence_kind": "chemical_structure_recognition_v1",
        "passed": True,
        "normalized_source_sha256": hashlib.sha256(_jpeg()).hexdigest(),
        "graph_sha256": semantic["graph_sha256"],
        "abbreviations": [_abbreviation()],
        "abbreviation_evidence_sha256": canonical_sha256([_abbreviation()]),
        "abbreviation_policy_version": "chemical-abbreviation-v1",
        "abbreviation_count": 1,
        "provider": "gemini",
        "model": "structure-test-v1",
        "response_sha256": "2" * 64,
        "verifier_version": "chemical-structure-v1",
        "attempts": 1,
    }


def _saved_evidence(
    semantic: dict[str, object], recognition: dict[str, object]
) -> dict[str, object]:
    from src.education.visual_semantic_contract import canonical_sha256

    return {
        "evidence_kind": "standalone_chemical_structure_saved_v1",
        "passed": True,
        "saved_file_sha256": "3" * 64,
        "page_number": 1,
        "image_xref": 7,
        "occurrence_ordinal": 0,
        "struct_parent": 4,
        "mcid": 8,
        "graph_sha256": semantic["graph_sha256"],
        "description_sha256": semantic["description_sha256"],
        "abbreviation_evidence_sha256": recognition["abbreviation_evidence_sha256"],
        "alt_text_sha256": hashlib.sha256(
            semantic["description"]["summary"].encode("utf-8")
        ).hexdigest(),
        "image_stream_sha256": "1" * 64,
        "attachment_sha256": semantic["graph_sha256"],
        "metadata_sha256": canonical_sha256(
            {
                "graph_sha256": semantic["graph_sha256"],
                "graph_identifier": semantic["graph"]["graph_identifier"],
                "description_sha256": semantic["description_sha256"],
                "attachment_sha256": semantic["graph_sha256"],
                "abbreviation_evidence_sha256": recognition[
                    "abbreviation_evidence_sha256"
                ],
                "abbreviation_policy_version": "chemical-abbreviation-v1",
            }
        ),
        "render_signatures": [[144, 200, 140, 600, 7, "5" * 64]],
    }


def _pdf_contract() -> dict[str, object]:
    from src.education.visual_semantic_contract import canonical_sha256

    semantic = _semantic()
    recognition = _recognition_evidence(semantic)
    specialist = {
        "contract_kind": "chemical_structure",
        "locator": _locator(),
        "semantic_output": semantic,
        "normalized_source_sha256": hashlib.sha256(_jpeg()).hexdigest(),
        "recognition_evidence": recognition,
    }
    value = {
        **{
            key: item
            for key, item in specialist.items()
            if key != "recognition_evidence"
        },
        "verification_evidence": [
            recognition,
            _saved_evidence(semantic, recognition),
        ],
        "specialist_sha256": canonical_sha256(specialist),
    }
    value["contract_sha256"] = canonical_sha256(value)
    return value


def test_complete_pdf_contract_is_public_and_binds_all_evidence():
    from src.education.visual_semantic_contract import (
        ChemicalStructurePdfContract,
        VisualSemanticContractAdapter,
    )

    contract = VisualSemanticContractAdapter.validate_python(_pdf_contract())
    assert isinstance(contract, ChemicalStructurePdfContract)
    assert contract.verification_evidence[0].abbreviation_count == 1


def test_public_schema_exposes_no_second_molecular_identity():
    from src.education.visual_semantic_contract import ChemicalStructurePdfContract

    schema = json.dumps(
        ChemicalStructurePdfContract.model_json_schema(),
        sort_keys=True,
    ).lower()
    for forbidden in ("smiles", "inchi", "molfile", "systematic_name"):
        assert forbidden not in schema


@pytest.mark.parametrize(
    ("evidence_index", "field", "replacement"),
    [
        (0, "passed", False),
        (0, "graph_sha256", "0" * 64),
        (0, "abbreviation_evidence_sha256", "0" * 64),
        (1, "graph_sha256", "0" * 64),
        (1, "description_sha256", "0" * 64),
        (1, "attachment_sha256", "0" * 64),
        (1, "metadata_sha256", "0" * 64),
    ],
)
def test_complete_pdf_contract_rejects_tampering(evidence_index, field, replacement):
    from src.education.visual_semantic_contract import VisualSemanticContractAdapter

    value = deepcopy(_pdf_contract())
    value["verification_evidence"][evidence_index][field] = replacement
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python(value)


def test_durable_contract_revalidates_source_token_mapping_against_graph():
    from src.education.visual_semantic_contract import VisualSemanticContractAdapter

    value = _pdf_contract()
    value["verification_evidence"][0]["abbreviations"][0]["anchor_atom_id"] = "outside"
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python(value)
