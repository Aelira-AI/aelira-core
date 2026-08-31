"""Contract tests for verified commutative-diagram PDF recognition."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import get_args

import pytest
from pydantic import ValidationError


def _triangle() -> dict[str, object]:
    return {
        "contract_kind": "commutative_diagram_v1",
        "nodes": [{"node_id": "a"}, {"node_id": "b"}, {"node_id": "c"}],
        "edges": [
            {
                "edge_id": "f",
                "source_node_id": "a",
                "target_node_id": "b",
                "direction": "directed",
            },
            {
                "edge_id": "g",
                "source_node_id": "b",
                "target_node_id": "c",
                "direction": "directed",
            },
            {
                "edge_id": "h",
                "source_node_id": "a",
                "target_node_id": "c",
                "direction": "directed",
            },
        ],
        "labels": [
            {
                "label_id": f"label-{value}",
                "text": value.upper(),
                "target_kind": "node",
                "target_id": value,
            }
            for value in "abc"
        ]
        + [
            {
                "label_id": f"label-{value}",
                "text": value,
                "target_kind": "edge",
                "target_id": value,
            }
            for value in "fgh"
        ],
        "paths": [
            {
                "path_id": "direct",
                "start_node_id": "a",
                "end_node_id": "c",
                "edge_ids": ["h"],
            },
            {
                "path_id": "composed",
                "start_node_id": "a",
                "end_node_id": "c",
                "edge_ids": ["f", "g"],
            },
        ],
        "relations": [
            {
                "relation_id": "triangle-commutes",
                "path_ids": ["direct", "composed"],
            }
        ],
        "layout": [],
        "unresolved_crossings": [],
    }


def _occurrence_id(*, bbox=(10.0, 20.0, 110.0, 90.0)) -> str:
    identity = "1|7|0|0|" + ",".join(f"{value:.6f}" for value in bbox)
    return "imgocc-v1-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _locator() -> dict[str, object]:
    bbox = (10.0, 20.0, 110.0, 90.0)
    return {
        "source_kind": "embedded_image_occurrence",
        "page_number": 1,
        "image_xref": 7,
        "image_index": 0,
        "occurrence_ordinal": 0,
        "bbox": bbox,
        "image_stream_sha256": "1" * 64,
        "occurrence_id": _occurrence_id(bbox=bbox),
    }


def _jpeg() -> bytes:
    return b"\xff\xd8bounded-diagram-raster\xff\xd9"


def _request_dict() -> dict[str, object]:
    payload = _jpeg()
    return {
        "request_kind": "commutative_diagram_recognition_v1",
        "locator": _locator(),
        "mime_type": "image/jpeg",
        "image_bytes": payload,
        "normalized_source_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _provider_json(graph=None) -> str:
    return json.dumps(
        {
            "classification": "commutative_diagram",
            "graph": graph if graph is not None else _triangle(),
        },
        separators=(",", ":"),
    )


class _Client:
    purpose = "alt_text"
    provider = "gemini"
    model = "diagram-test-v1"

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
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRequestV1,
        CommutativeDiagramRecognizer,
    )

    request = CommutativeDiagramRecognitionRequestV1.model_validate(_request_dict())
    client = client or _Client([{"success": True, "content": _provider_json()}])
    return CommutativeDiagramRecognizer(client).recognize(request)


def test_request_is_frozen_exact_and_digest_bound():
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRequestV1,
    )

    request = CommutativeDiagramRecognitionRequestV1.model_validate(_request_dict())
    assert request.locator.source_kind == "embedded_image_occurrence"
    with pytest.raises(ValidationError):
        request.mime_type = "image/png"

    extra = {**_request_dict(), "callback": "active"}
    with pytest.raises(ValidationError):
        CommutativeDiagramRecognitionRequestV1.model_validate(extra)

    forged = {**_request_dict(), "normalized_source_sha256": "0" * 64}
    with pytest.raises(ValidationError, match="normalized_source_sha256"):
        CommutativeDiagramRecognitionRequestV1.model_validate(forged)


@pytest.mark.parametrize(
    "change",
    [
        {"mime_type": "image/png"},
        {"image_bytes": b"not-jpeg"},
        {"image_bytes": b"\xff\xd8" + b"x" * 4_194_305},
        {"request_kind": "printed_equation"},
    ],
)
def test_request_rejects_unbounded_or_wrong_purpose_payload(change):
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRequestV1,
    )

    value = {**_request_dict(), **change}
    if "image_bytes" in change:
        value["normalized_source_sha256"] = hashlib.sha256(
            change["image_bytes"]
        ).hexdigest()
    with pytest.raises(ValidationError):
        CommutativeDiagramRecognitionRequestV1.model_validate(value)


def test_recognizer_returns_verified_graph_and_bounded_provenance():
    recognition = _recognize()

    assert recognition.recognition_kind == "commutative_diagram_recognition_v1"
    assert recognition.graph.contract_kind == "commutative_diagram_v1"
    assert recognition.graph_sha256 == recognition.graph.canonical_sha256
    assert recognition.provider == "gemini"
    assert recognition.model == "diagram-test-v1"
    assert recognition.attempts == 1
    assert recognition.verifier_version == "commutative-diagram-v1"


def test_recognizer_rejects_wrong_client_purpose_before_call():
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRejected,
        CommutativeDiagramRecognitionRequestV1,
        CommutativeDiagramRecognizer,
    )

    client = _Client([{"success": True, "content": _provider_json()}])
    client.purpose = "remediation"
    request = CommutativeDiagramRecognitionRequestV1.model_validate(_request_dict())
    with pytest.raises(CommutativeDiagramRecognitionRejected, match="purpose_mismatch"):
        CommutativeDiagramRecognizer(client).recognize(request)
    assert client.calls == 0


@pytest.mark.parametrize(
    "content",
    [
        "prefix " + _provider_json(),
        _provider_json() + "\n",
        '{"classification":"commutative_diagram","classification":"x","graph":{}}',
        '{"classification":"commutative_diagram","graph":{},"confidence":0.99}',
        '{"classification":"not_commutative_diagram","graph":{}}',
        "[]",
    ],
)
def test_recognizer_rejects_prose_duplicates_and_unknown_fields(content):
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRejected,
    )

    client = _Client([{"success": True, "content": content}])
    with pytest.raises(
        CommutativeDiagramRecognitionRejected, match="invalid_provider_response"
    ):
        _recognize(client)


def test_recognizer_rejects_topology_the_graph_verifier_refuses():
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRejected,
    )

    graph = _triangle()
    graph["unresolved_crossings"] = [{"crossing_id": "cross", "edge_ids": ["f", "h"]}]
    client = _Client([{"success": True, "content": _provider_json(graph)}])
    with pytest.raises(CommutativeDiagramRecognitionRejected, match="graph_rejected"):
        _recognize(client)


@pytest.mark.parametrize("field", ["provider", "model"])
def test_recognizer_requires_bounded_provider_identity(field):
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRejected,
    )

    client = _Client([{"success": True, "content": _provider_json()}])
    setattr(client, field, "")
    with pytest.raises(CommutativeDiagramRecognitionRejected, match="identity_missing"):
        _recognize(client)


def test_transient_provider_failure_retries_once():
    client = _Client(
        [RuntimeError("transient"), {"success": True, "content": _provider_json()}]
    )
    recognition = _recognize(client)
    assert recognition.attempts == 2
    assert client.calls == 2


def test_provider_failure_exhaustion_fails_closed():
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRejected,
    )

    client = _Client([RuntimeError("one"), RuntimeError("two")])
    with pytest.raises(CommutativeDiagramRecognitionRejected, match="provider_failure"):
        _recognize(client)
    assert client.calls == 2


def test_not_diagram_classification_stays_open_without_graph():
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramRecognitionRejected,
    )

    content = '{"classification":"not_commutative_diagram","graph":null}'
    client = _Client([{"success": True, "content": content}])
    with pytest.raises(
        CommutativeDiagramRecognitionRejected, match="not_commutative_diagram"
    ):
        _recognize(client)


def test_semantic_output_is_accepted_by_public_typed_adapter():
    from src.education.commutative_diagram_pdf import (
        commutative_diagram_semantic_output,
    )
    from src.education.visual_semantic_contract import (
        CommutativeDiagramSemanticV1,
        SemanticOutputAdapter,
    )

    semantic = commutative_diagram_semantic_output(_recognize().graph)
    restored = SemanticOutputAdapter.validate_python(semantic.model_dump(mode="json"))
    assert isinstance(restored, CommutativeDiagramSemanticV1)
    assert restored.graph_sha256 == restored.graph.canonical_sha256
    assert restored.description.graph_sha256 == restored.graph_sha256


def test_semantic_output_rejects_tampered_graph_description_or_html():
    from src.education.commutative_diagram_pdf import (
        commutative_diagram_semantic_output,
    )
    from src.education.visual_semantic_contract import CommutativeDiagramSemanticV1

    semantic = commutative_diagram_semantic_output(_recognize().graph)
    value = semantic.model_dump(mode="json")
    for field, replacement in (
        ("graph_sha256", "0" * 64),
        ("description_sha256", "0" * 64),
        ("rendered_html", value["rendered_html"] + "<script>bad()</script>"),
    ):
        tampered = deepcopy(value)
        tampered[field] = replacement
        with pytest.raises(ValidationError):
            CommutativeDiagramSemanticV1.model_validate(tampered)


def test_visual_adapters_still_reject_unknown_discriminators():
    from src.education.visual_semantic_contract import (
        SemanticOutputAdapter,
        VerificationEvidenceAdapter,
        VisualSemanticContractAdapter,
    )

    with pytest.raises(ValidationError):
        SemanticOutputAdapter.validate_python({"semantic_kind": "diagram_v99"})
    with pytest.raises(ValidationError):
        VerificationEvidenceAdapter.validate_python({"evidence_kind": "diagram_v99"})
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python({"contract_kind": "diagram_v99"})


def test_printed_equation_union_variant_remains_public():
    from src.education.visual_semantic_contract import (
        PrintedEquationContract,
        VisualSemanticContract,
    )

    variants = get_args(VisualSemanticContract)[0]
    assert variants is PrintedEquationContract or PrintedEquationContract in get_args(
        variants
    )


def _semantic_dict() -> dict[str, object]:
    from src.education.commutative_diagram_pdf import (
        commutative_diagram_semantic_output,
    )

    return commutative_diagram_semantic_output(_recognize().graph).model_dump(
        mode="json"
    )


def _recognition_evidence(semantic: dict[str, object]) -> dict[str, object]:
    return {
        "evidence_kind": "commutative_diagram_recognition_v1",
        "passed": True,
        "normalized_source_sha256": hashlib.sha256(_jpeg()).hexdigest(),
        "graph_sha256": semantic["graph_sha256"],
        "provider": "gemini",
        "model": "diagram-test-v1",
        "response_sha256": "2" * 64,
        "verifier_version": "commutative-diagram-v1",
        "attempts": 1,
    }


def _saved_evidence(semantic: dict[str, object]) -> dict[str, object]:
    from src.education.visual_semantic_contract import canonical_sha256

    return {
        "evidence_kind": "standalone_diagram_saved_v1",
        "passed": True,
        "saved_file_sha256": "3" * 64,
        "page_number": 1,
        "image_xref": 7,
        "occurrence_ordinal": 0,
        "struct_parent": 4,
        "mcid": 8,
        "graph_sha256": semantic["graph_sha256"],
        "description_sha256": semantic["description_sha256"],
        "rendered_html_sha256": semantic["rendered_html_sha256"],
        "alt_text_sha256": hashlib.sha256(
            semantic["description"]["summary"].encode("utf-8")
        ).hexdigest(),
        "image_stream_sha256": "1" * 64,
        "attachment_sha256": canonical_sha256(semantic["graph"]),
        "metadata_sha256": canonical_sha256(
            {
                "graph_sha256": semantic["graph_sha256"],
                "description_sha256": semantic["description_sha256"],
                "rendered_html_sha256": semantic["rendered_html_sha256"],
            }
        ),
        "render_signatures": [[144, 200, 140, 600, 7, "5" * 64]],
    }


def _pdf_contract() -> dict[str, object]:
    from src.education.visual_semantic_contract import canonical_sha256

    semantic = _semantic_dict()
    specialist = {
        "contract_kind": "commutative_diagram",
        "locator": _locator(),
        "semantic_output": semantic,
        "normalized_source_sha256": hashlib.sha256(_jpeg()).hexdigest(),
        "recognition_evidence": _recognition_evidence(semantic),
    }
    value = {
        **{
            key: item
            for key, item in specialist.items()
            if key != "recognition_evidence"
        },
        "verification_evidence": [
            _recognition_evidence(semantic),
            _saved_evidence(semantic),
        ],
        "specialist_sha256": canonical_sha256(specialist),
    }
    value["contract_sha256"] = canonical_sha256(value)
    return value


def test_complete_pdf_contract_is_public_and_binds_all_evidence():
    from src.education.visual_semantic_contract import (
        CommutativeDiagramPdfContract,
        VisualSemanticContractAdapter,
    )

    contract = VisualSemanticContractAdapter.validate_python(_pdf_contract())
    assert isinstance(contract, CommutativeDiagramPdfContract)
    assert (
        contract.semantic_output.graph_sha256
        == contract.verification_evidence[0].graph_sha256
    )


@pytest.mark.parametrize(
    ("evidence_index", "field", "replacement"),
    [
        (0, "passed", False),
        (0, "graph_sha256", "0" * 64),
        (0, "normalized_source_sha256", "0" * 64),
        (1, "graph_sha256", "0" * 64),
        (1, "description_sha256", "0" * 64),
        (1, "attachment_sha256", "0" * 64),
        (1, "image_stream_sha256", "0" * 64),
    ],
)
def test_complete_pdf_contract_rejects_disagreement_or_stale_evidence(
    evidence_index, field, replacement
):
    from src.education.visual_semantic_contract import VisualSemanticContractAdapter

    value = deepcopy(_pdf_contract())
    value["verification_evidence"][evidence_index][field] = replacement
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python(value)


def test_complete_pdf_contract_requires_exact_recognition_and_saved_pair():
    from src.education.visual_semantic_contract import VisualSemanticContractAdapter

    value = _pdf_contract()
    value["verification_evidence"] = value["verification_evidence"][:1]
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python(value)

    value = _pdf_contract()
    value["verification_evidence"] = [value["verification_evidence"][0]] * 2
    with pytest.raises(ValidationError):
        VisualSemanticContractAdapter.validate_python(value)


def test_specialist_digest_binds_provider_and_verifier_policy():
    from src.education.visual_semantic_contract import (
        VisualSemanticContractAdapter,
        canonical_sha256,
    )

    value = _pdf_contract()
    value["verification_evidence"][0]["provider"] = "different-provider"
    value["contract_sha256"] = canonical_sha256(
        {key: item for key, item in value.items() if key != "contract_sha256"}
    )
    with pytest.raises(ValidationError, match="specialist_sha256"):
        VisualSemanticContractAdapter.validate_python(value)
