"""Frozen corpus and deterministic suitability boundary for handwritten math."""

from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from src.education.handwritten_math_suitability import (
    CORPUS_SCHEMA_VERSION,
    POLICY_SHA256,
    POLICY_VERSION,
    HandwrittenMathCorpusManifest,
    HandwrittenMathSuitabilityEvidence,
    SuitabilityInputRejected,
    classify_handwritten_math_suitability,
    ensure_hmer_eligible,
    load_corpus_manifest,
    suitability_policy_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = ROOT / "tests" / "fixtures" / "handwritten_math"
MANIFEST_PATH = CORPUS_ROOT / "manifest.json"
GENERATOR_PATH = ROOT / "scripts" / "generate_handwritten_math_corpus.py"
EVALUATOR_PATH = ROOT / "scripts" / "evaluate_handwritten_math_corpus.py"
DOC_PATH = ROOT / "docs" / "document-remediation" / "handwritten-math.md"
DOC_INDEX_PATH = ROOT / "docs" / "document-remediation" / "README.md"


def _manifest() -> HandwrittenMathCorpusManifest:
    return load_corpus_manifest(MANIFEST_PATH)


def _fixture_bytes(fixture) -> bytes:
    return (CORPUS_ROOT / fixture.path).read_bytes()


def test_manifest_is_exact_versioned_and_rejects_ambiguous_entries():
    manifest = _manifest()
    assert manifest.schema_version == CORPUS_SCHEMA_VERSION
    assert manifest.policy_version == POLICY_VERSION
    assert len(manifest.fixtures) >= 10

    raw = json.loads(MANIFEST_PATH.read_text())
    with pytest.raises(ValidationError):
        HandwrittenMathCorpusManifest.model_validate({**raw, "extra": True})

    duplicate = json.loads(MANIFEST_PATH.read_text())
    duplicate["fixtures"].append(duplicate["fixtures"][0])
    with pytest.raises(ValidationError, match="fixture ids must be unique"):
        HandwrittenMathCorpusManifest.model_validate(duplicate)

    duplicate_path = json.loads(MANIFEST_PATH.read_text())
    duplicate_path["fixtures"][1]["path"] = duplicate_path["fixtures"][0]["path"]
    with pytest.raises(ValidationError, match="fixture paths must be unique"):
        HandwrittenMathCorpusManifest.model_validate(duplicate_path)


def test_manifest_loader_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":"one","schema_version":"two"}')
    with pytest.raises(ValueError, match="not exact JSON"):
        load_corpus_manifest(path)


def test_manifest_documents_provenance_rights_categories_and_hashes():
    manifest = _manifest()
    categories = {fixture.category for fixture in manifest.fixtures}
    assert {
        "legible_handwriting",
        "low_contrast",
        "strike_through",
        "annotation",
        "multiple_lines",
        "diagram",
        "unsupported_style",
        "non_math_handwriting",
        "visually_similar_non_math",
    } <= categories

    for fixture in manifest.fixtures:
        assert fixture.author == "Aelira project contributors"
        assert fixture.generation_method == "deterministic synthetic stroke drawing"
        assert fixture.license == "AGPL-3.0-only"
        assert fixture.source_class == "project_authored_synthetic"
        payload = _fixture_bytes(fixture)
        assert hashlib.sha256(payload).hexdigest() == fixture.sha256
        with Image.open(CORPUS_ROOT / fixture.path) as image:
            assert image.format == "PNG"
            assert image.info == {}


def test_fixture_generator_reproduces_every_asset_and_manifest(tmp_path):
    subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--output-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    expected = sorted(
        path.relative_to(CORPUS_ROOT)
        for path in CORPUS_ROOT.rglob("*")
        if path.is_file()
    )
    actual = sorted(
        path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()
    )
    assert actual == expected
    for relative in expected:
        assert (tmp_path / relative).read_bytes() == (
            CORPUS_ROOT / relative
        ).read_bytes()


def test_classifier_matches_frozen_corpus_without_reading_labels(monkeypatch):
    import src.education.handwritten_math_suitability as module

    monkeypatch.setattr(
        module,
        "load_corpus_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("classifier must not consult corpus labels")
        ),
    )
    for fixture in _manifest().fixtures:
        evidence = classify_handwritten_math_suitability(_fixture_bytes(fixture))
        assert evidence.disposition == fixture.expected_disposition, fixture.id
        if evidence.disposition != "eligible":
            assert evidence.reason_codes


def test_classifier_source_has_no_provider_ocr_or_network_dependency():
    source = (
        ROOT / "src" / "education" / "handwritten_math_suitability.py"
    ).read_text()
    for forbidden in (
        "pytesseract",
        "requests",
        "httpx",
        "socket",
        "analyze_image",
    ):
        assert forbidden not in source
    assert "expected_disposition" not in inspect.getsource(
        classify_handwritten_math_suitability
    )


def test_classifier_is_deterministic_and_source_policy_bound():
    fixture = next(
        fixture
        for fixture in _manifest().fixtures
        if fixture.expected_disposition == "eligible"
    )
    payload = _fixture_bytes(fixture)
    first = classify_handwritten_math_suitability(payload)
    second = classify_handwritten_math_suitability(payload)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.policy_version == POLICY_VERSION
    assert first.policy_sha256 == POLICY_SHA256 == suitability_policy_sha256()
    assert first.source_sha256 == hashlib.sha256(payload).hexdigest()

    changed_source = first.model_copy(update={"source_sha256": "0" * 64})
    with pytest.raises(ValueError, match="evidence digest"):
        HandwrittenMathSuitabilityEvidence.model_validate(
            changed_source.model_dump(mode="json")
        )
    changed_policy = first.model_copy(update={"policy_sha256": "0" * 64})
    with pytest.raises(ValueError, match="policy digest"):
        HandwrittenMathSuitabilityEvidence.model_validate(
            changed_policy.model_dump(mode="json")
        )


def test_policy_mapping_cannot_be_mutated_at_runtime():
    import src.education.handwritten_math_suitability as module

    with pytest.raises(TypeError):
        module._POLICY["eligible_component_count_min"] = 0


def test_hmer_guard_accepts_only_exact_eligible_source():
    manifest = _manifest()
    eligible = next(
        fixture
        for fixture in manifest.fixtures
        if fixture.expected_disposition == "eligible"
    )
    held = next(
        fixture
        for fixture in manifest.fixtures
        if fixture.expected_disposition == "human_review"
    )
    eligible_bytes = _fixture_bytes(eligible)
    held_bytes = _fixture_bytes(held)
    eligible_evidence = classify_handwritten_math_suitability(eligible_bytes)
    held_evidence = classify_handwritten_math_suitability(held_bytes)

    ensure_hmer_eligible(eligible_bytes, eligible_evidence)
    with pytest.raises(SuitabilityInputRejected, match="source_mismatch"):
        ensure_hmer_eligible(held_bytes, eligible_evidence)
    with pytest.raises(SuitabilityInputRejected, match="not_eligible"):
        ensure_hmer_eligible(held_bytes, held_evidence)

    forged = held_evidence.model_dump(mode="json")
    forged["disposition"] = "eligible"
    forged["reason_codes"] = []
    forged.pop("evidence_sha256")
    forged["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            forged,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    forged_evidence = HandwrittenMathSuitabilityEvidence.model_validate(forged)
    with pytest.raises(SuitabilityInputRejected, match="evidence_mismatch"):
        ensure_hmer_eligible(held_bytes, forged_evidence)


def test_supported_jpeg_is_decoded_and_bound_to_its_exact_bytes():
    fixture = next(
        fixture
        for fixture in _manifest().fixtures
        if fixture.expected_disposition == "eligible"
    )
    with Image.open(BytesIO(_fixture_bytes(fixture))) as image:
        output = BytesIO()
        image.save(output, format="JPEG", quality=95, optimize=False, progressive=False)
    payload = output.getvalue()
    evidence = classify_handwritten_math_suitability(payload)
    assert evidence.image_format == "JPEG"
    assert evidence.disposition == "eligible"
    ensure_hmer_eligible(payload, evidence)


def test_decoded_pixel_budget_is_enforced_before_classification():
    output = BytesIO()
    Image.new("L", (1025, 1025), 255).save(output, format="PNG")
    with pytest.raises(SuitabilityInputRejected, match="image_dimensions_unsupported"):
        classify_handwritten_math_suitability(output.getvalue())


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not an image",
        b"GIF89a" + b"\0" * 64,
        b"\x89PNG\r\n\x1a\n" + b"\0" * 4_194_305,
    ],
    ids=("empty", "text", "unsupported-gif", "oversized-png"),
)
def test_malformed_unsupported_and_oversized_inputs_fail_closed(payload):
    with pytest.raises(SuitabilityInputRejected):
        classify_handwritten_math_suitability(payload)


def test_negative_corpus_has_zero_eligible_false_positives():
    negatives = [
        fixture
        for fixture in _manifest().fixtures
        if fixture.category in {"non_math_handwriting", "visually_similar_non_math"}
    ]
    assert len(negatives) >= 3
    assert all(
        classify_handwritten_math_suitability(_fixture_bytes(fixture)).disposition
        != "eligible"
        for fixture in negatives
    )


def test_full_evaluator_is_canonical_and_fails_on_fixture_tampering(tmp_path):
    first = subprocess.run(
        [sys.executable, str(EVALUATOR_PATH), "--manifest", str(MANIFEST_PATH)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [sys.executable, str(EVALUATOR_PATH), "--manifest", str(MANIFEST_PATH)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["passed"] is True
    assert report["mismatches"] == []
    assert report["eligible_false_positives"] == 0
    assert report["policy_sha256"] == POLICY_SHA256

    tampered_root = tmp_path / "corpus"
    subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--output-dir", str(tampered_root)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    fixture_path = next((tampered_root / "images").glob("*.png"))
    fixture_path.write_bytes(fixture_path.read_bytes() + b"tampered")
    failed = subprocess.run(
        [
            sys.executable,
            str(EVALUATOR_PATH),
            "--manifest",
            str(tampered_root / "manifest.json"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert "fixture_digest_mismatch" in failed.stderr


def test_external_prediction_replay_is_exact_and_digest_bound(tmp_path):
    manifest = _manifest()
    baseline = subprocess.run(
        [
            sys.executable,
            str(EVALUATOR_PATH),
            "--manifest",
            str(MANIFEST_PATH),
            "--subset",
            "ci",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(baseline.stdout)
    by_id = {fixture.id: fixture for fixture in manifest.fixtures}
    predictions = {
        "corpus_sha256": report["corpus_sha256"],
        "model": "offline-replay-v1",
        "policy_sha256": POLICY_SHA256,
        "predictions": [
            {
                "disposition": by_id[fixture_id].expected_disposition,
                "fixture_id": fixture_id,
            }
            for fixture_id in manifest.ci_subset
        ],
        "provider": "fixture",
        "schema_version": "handwritten-math-predictions-v1",
    }
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(json.dumps(predictions, sort_keys=True))

    replay = subprocess.run(
        [
            sys.executable,
            str(EVALUATOR_PATH),
            "--manifest",
            str(MANIFEST_PATH),
            "--subset",
            "ci",
            "--predictions",
            str(prediction_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    replay_report = json.loads(replay.stdout)
    assert replay_report["passed"] is True
    assert replay_report["evaluation_source"]["kind"] == "frozen_predictions"
    assert replay_report["evaluation_source"]["provider"] == "fixture"
    assert replay_report["evaluation_source"]["model"] == "offline-replay-v1"
    assert len(replay_report["evaluation_source"]["prediction_set_sha256"]) == 64

    predictions["policy_sha256"] = "0" * 64
    prediction_path.write_text(json.dumps(predictions, sort_keys=True))
    stale = subprocess.run(
        [
            sys.executable,
            str(EVALUATOR_PATH),
            "--manifest",
            str(MANIFEST_PATH),
            "--subset",
            "ci",
            "--predictions",
            str(prediction_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert stale.returncode != 0
    assert "predictions_policy_mismatch" in stale.stderr


def test_documentation_freezes_limits_and_human_review_boundary():
    text = DOC_PATH.read_text()
    for required in (
        "Suitability is not recognition",
        "always require human review",
        "python scripts/evaluate_handwritten_math_corpus.py",
        "--predictions",
        "non-Latin",
        "motor disabilities",
        "student work",
    ):
        assert required in text
    assert "[handwritten-math suitability corpus](handwritten-math.md)" in (
        DOC_INDEX_PATH.read_text()
    )
