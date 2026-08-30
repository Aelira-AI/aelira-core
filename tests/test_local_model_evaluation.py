from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.evaluate_local_models import (
    EVALUATOR_VERSION,
    MATRIX_PATH,
    REPORT_PATH,
    classify_support,
    cosine_similarity,
    create_client,
    load_matrix,
    summarize_runs,
    validate_chart_response,
    validate_code_response,
    validate_embedding_vectors,
    validate_matrix,
    validate_report,
    validate_scanned_document_response,
    validate_text_response,
    verify_documentation_contract,
)
from src.ai.providers.manager import ProviderManager
from src.ai.providers.ollama_provider import OllamaProvider
from src.ai.providers.types import (
    OLLAMA_EVALUATED_MODELS,
    PROVIDER_MODELS,
    ProviderConfig,
    ProviderType,
)
from src.config.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_matrix_covers_each_supported_lane_with_exact_pinned_models():
    matrix = load_matrix()

    assert {entry["lane"]: entry["model"] for entry in matrix["models"]} == {
        "text": "gemma3:4b",
        "code": "qwen2.5-coder:7b",
        "vision": "qwen2.5vl:3b",
        "embeddings": "nomic-embed-text:latest",
    }
    assert validate_matrix(matrix) == []


def test_runtime_defaults_use_the_evaluated_matrix(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "OLLAMA_TEXT_MODEL",
        "OLLAMA_CODE_MODEL",
        "OLLAMA_VISION_MODEL",
        "OLLAMA_EMBEDDING_MODEL",
        "OLLAMA_FALLBACK_TEXT",
        "OLLAMA_FALLBACK_CODE",
        "OLLAMA_FALLBACK_VISION",
    ):
        monkeypatch.delenv(name, raising=False)

    config = ProviderConfig.default_for_provider(ProviderType.OLLAMA)
    provider = OllamaProvider(ProviderConfig(provider_type=ProviderType.OLLAMA))
    manager_config = ProviderManager().configs[ProviderType.OLLAMA]

    for candidate in (config, provider, manager_config):
        assert candidate.text_model == OLLAMA_EVALUATED_MODELS["text"]
        assert candidate.code_model == OLLAMA_EVALUATED_MODELS["code"]
        assert candidate.vision_model == OLLAMA_EVALUATED_MODELS["vision"]
        assert candidate.embedding_model == OLLAMA_EVALUATED_MODELS["embeddings"]

    assert (
        Settings.model_fields["ollama_text_model"].default
        == OLLAMA_EVALUATED_MODELS["text"]
    )
    assert (
        Settings.model_fields["ollama_code_model"].default
        == OLLAMA_EVALUATED_MODELS["code"]
    )
    assert (
        Settings.model_fields["ollama_vision_model"].default
        == OLLAMA_EVALUATED_MODELS["vision"]
    )
    assert Settings.model_fields["ollama_embedding_model"].default == (
        OLLAMA_EVALUATED_MODELS["embeddings"]
    )


def test_public_configuration_surfaces_use_the_evaluated_matrix():
    example = (ROOT / ".env.example").read_text()
    expected_environment = {
        "text": "OLLAMA_TEXT_MODEL",
        "code": "OLLAMA_CODE_MODEL",
        "vision": "OLLAMA_VISION_MODEL",
        "embeddings": "OLLAMA_EMBEDDING_MODEL",
    }
    for lane, name in expected_environment.items():
        expected = OLLAMA_EVALUATED_MODELS[lane]
        assert f"# {name}={expected}" in example

    for compose_name in (
        "docker-compose.dev.yml",
        "docker-compose.quickstart.yml",
        "docker-compose.prod.yml",
    ):
        compose = (ROOT / compose_name).read_text()
        for lane, name in expected_environment.items():
            expected = OLLAMA_EVALUATED_MODELS[lane]
            assert f"{name}: ${{{name}:-{expected}}}" in compose


def test_static_ollama_catalog_contains_only_fixture_evaluated_models():
    assert set(PROVIDER_MODELS[ProviderType.OLLAMA]) == set(
        OLLAMA_EVALUATED_MODELS.values()
    )


def test_hardware_helper_does_not_invent_a_smaller_supported_profile():
    constrained = OllamaProvider.get_recommended_models_for_hardware(
        ram_gb=8, has_gpu=False
    )

    assert constrained["profile"] == "evaluated"
    assert constrained["hardware_verified"] is False
    assert constrained["text_model"] == OLLAMA_EVALUATED_MODELS["text"]
    assert constrained["code_model"] == OLLAMA_EVALUATED_MODELS["code"]
    assert constrained["vision_model"] == OLLAMA_EVALUATED_MODELS["vision"]
    assert constrained["embedding_model"] == OLLAMA_EVALUATED_MODELS["embeddings"]
    nominal_reference = OllamaProvider.get_recommended_models_for_hardware(
        ram_gb=18, has_gpu=True
    )

    assert "target host" in constrained["reason"]
    assert nominal_reference["hardware_verified"] is False


def test_matrix_uses_tracked_public_fixture_paths():
    matrix = load_matrix()
    source_paths = {
        case["fixture"]
        for model in matrix["models"]
        for case in model["cases"]
        if "fixture" in case
    }

    assert source_paths == {
        "tests/fixtures/html/form_issues.html",
        "tests/fixtures/html/missing_alt_text.html",
        "tests/fixtures/pdfs/simple_syllabus.pdf",
        "tests/fixtures/sample_chart.png",
    }
    assert all((ROOT / path).is_file() for path in source_paths)


@pytest.mark.parametrize(
    "host",
    [
        "https://example.org:11434",
        "http://10.0.0.5:11434",
        "http://ollama:11434",
        "http://127.0.0.1:11434/path",
        "http://user:password@127.0.0.1:11434",
    ],
)
def test_client_rejects_non_loopback_or_credentialed_hosts(host: str):
    with pytest.raises(ValueError, match="loopback"):
        create_client(host, timeout=30)


def test_client_disables_redirects_and_environment_proxies():
    with patch("scripts.evaluate_local_models.ollama.Client") as constructor:
        create_client("http://localhost:11434", timeout=30)

    constructor.assert_called_once_with(
        host="http://127.0.0.1:11434",
        timeout=30,
        follow_redirects=False,
        trust_env=False,
    )


def test_text_validator_requires_issue_fix_and_review_boundary():
    passing = validate_text_response(
        {
            "issue": "The first image is missing alternative text.",
            "recommendation": "Add an alt attribute after confirming the image purpose.",
            "review_required": True,
        }
    )
    invented = validate_text_response(
        {
            "issue": "The first image is missing alternative text.",
            "recommendation": "Set alt to University campus at sunset.",
            "review_required": False,
        }
    )

    assert passing.passed is True
    assert passing.code == "passed"
    assert invented.passed is False
    assert invented.code == "human_review_boundary_missing"


def test_code_validator_parses_labels_and_preserves_controls():
    passing = validate_code_response(
        {
            "html": (
                '<form><label for="name">Name</label>'
                '<input id="name" type="text">'
                '<label for="email">Email</label>'
                '<input id="email" type="email">'
                '<button type="submit">Send</button></form>'
            )
        }
    )
    missing_label = validate_code_response(
        {
            "html": (
                '<form><input id="name" type="text">'
                '<input id="email" type="email">'
                '<button type="submit">Send</button></form>'
            )
        }
    )

    assert passing.passed is True
    assert passing.observations["labelled_inputs"] == 2
    assert missing_label.passed is False
    assert missing_label.code == "input_label_missing"


def test_chart_validator_requires_exact_fixture_facts():
    passing = validate_chart_response(
        {
            "title": "Quarterly Revenue ($M)",
            "series": [
                {"label": "Q1", "value": 65},
                {"label": "Q2", "value": 80},
                {"label": "Q3", "value": 55},
                {"label": "Q4", "value": 90},
            ],
        }
    )
    wrong_value = validate_chart_response(
        {
            "title": "Quarterly Revenue ($M)",
            "series": [
                {"label": "Q1", "value": 65},
                {"label": "Q2", "value": 80},
                {"label": "Q3", "value": 50},
                {"label": "Q4", "value": 90},
            ],
        }
    )

    assert passing.passed is True
    assert wrong_value.passed is False
    assert wrong_value.code == "chart_values_incorrect"


def test_scanned_document_validator_requires_page_specific_fields():
    passing = validate_scanned_document_response(
        {
            "course": "MATH 250: Introduction to Statistics",
            "term": "Fall 2025",
            "instructor": "Dr. Emily Chen",
            "office": "Mathematics Building Room 312",
            "office_hours": "Tuesday/Thursday 2:00-4:00 PM",
        }
    )
    generic = validate_scanned_document_response(
        {
            "course": "Statistics",
            "term": "2025",
            "instructor": "Emily",
            "office": "Room",
            "office_hours": "Afternoon",
        }
    )

    assert passing.passed is True
    assert generic.passed is False
    assert generic.code == "document_fields_incorrect"


def test_embedding_validator_requires_semantic_ranking():
    passing = validate_embedding_vectors(
        {
            "anchor": [1.0, 0.0, 0.0],
            "identical": [1.0, 0.0, 0.0],
            "related": [0.8, 0.6, 0.0],
            "unrelated": [0.0, 0.0, 1.0],
        }
    )
    failing = validate_embedding_vectors(
        {
            "anchor": [1.0, 0.0],
            "identical": [1.0, 0.0],
            "related": [0.1, 0.99],
            "unrelated": [0.9, 0.1],
        }
    )

    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert passing.passed is True
    assert passing.observations["identical_similarity"] == pytest.approx(1.0)
    assert failing.passed is False
    assert failing.code == "embedding_ranking_failed"


def test_run_summary_is_recomputed_from_retained_measurements():
    runs = [
        {"elapsed_ms": 1200, "passed": True},
        {"elapsed_ms": 800, "passed": True},
        {"elapsed_ms": 1000, "passed": True},
    ]

    assert summarize_runs(runs) == {
        "median_elapsed_ms": 1000,
        "max_elapsed_ms": 1200,
        "passed_runs": 3,
        "total_runs": 3,
    }


@pytest.mark.parametrize(
    ("code", "passed"),
    [
        ("model_missing", False),
        ("request_timeout", False),
        ("request_failed", False),
        ("malformed_response", False),
        ("passed", True),
    ],
)
def test_support_classification_fails_closed(code: str, passed: bool):
    cases = [
        {
            "lane": "text",
            "model": "gemma3:4b",
            "case_id": "missing-alt-explanation",
            "runs": [{"elapsed_ms": 1, "passed": passed, "code": code}],
        }
    ]

    support = classify_support(cases)

    assert support["text"]["status"] == ("supported" if passed else "unsupported")
    assert support["text"]["failure_modes"] == ([] if passed else [code])


def test_checked_in_report_is_complete_and_self_consistent():
    matrix = load_matrix()
    report = json.loads(REPORT_PATH.read_text())

    assert report["evaluator_version"] == EVALUATOR_VERSION
    assert validate_report(report, matrix) == []
    assert all(
        math.isfinite(run["elapsed_ms"]) and run["elapsed_ms"] >= 0
        for case in report["cases"]
        for run in case["runs"]
    )


def test_documentation_claims_match_checked_in_report():
    report = json.loads(REPORT_PATH.read_text())
    documentation = (ROOT / "docs/deployment/local-ai-models.md").read_text()

    assert verify_documentation_contract(documentation, report) == []


def test_documentation_contract_rejects_a_drifted_evaluated_default():
    report = json.loads(REPORT_PATH.read_text())
    documentation = (ROOT / "docs/deployment/local-ai-models.md").read_text()
    drifted = documentation.replace(
        "| Text | `gemma3:4b` | Identified",
        "| Text | `llama3.2:3b` | Identified",
        1,
    )

    assert "documentation evaluated defaults do not match the report" in (
        verify_documentation_contract(drifted, report)
    )


def test_matrix_and_report_paths_are_public_repository_artifacts():
    assert MATRIX_PATH == ROOT / "tests/fixtures/local_models/matrix.json"
    assert REPORT_PATH == ROOT / "docs/deployment/local-ai-model-results.json"
