#!/usr/bin/env python3
"""Verify and evaluate the frozen handwritten-math suitability corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.education.handwritten_math_suitability import (
    POLICY_SHA256,
    HandwrittenMathCorpusManifest,
    SuitabilityDisposition,
    classify_handwritten_math_suitability,
    load_corpus_manifest,
)


class FrozenPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    disposition: SuitabilityDisposition


class FrozenPredictionSet(BaseModel):
    """Exact external-model output surface; raw provider prose is never retained."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["handwritten-math-predictions-v1"]
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    predictions: tuple[FrozenPrediction, ...] = Field(min_length=1, max_length=256)

    @field_validator("provider", "model")
    @classmethod
    def _bounded_identity(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("prediction identity must be trimmed and printable")
        return value

    @model_validator(mode="after")
    def _unique_fixture_ids(self) -> "FrozenPredictionSet":
        fixture_ids = [prediction.fixture_id for prediction in self.predictions]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("prediction fixture ids must be unique")
        return self


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _corpus_sha256(manifest: HandwrittenMathCorpusManifest) -> str:
    return hashlib.sha256(_canonical(manifest.model_dump(mode="json"))).hexdigest()


def _load_predictions(
    path: Path,
    *,
    corpus_sha256: str,
    fixture_ids: set[str],
) -> FrozenPredictionSet:
    payload = path.read_bytes()
    if not payload or len(payload) > 512 * 1024:
        raise ValueError("predictions_size_unsupported")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("predictions_duplicate_json_key")
            result[key] = value
        return result

    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("predictions_invalid_json") from exc
    parsed = FrozenPredictionSet.model_validate(raw)
    if parsed.corpus_sha256 != corpus_sha256:
        raise ValueError("predictions_corpus_mismatch")
    if parsed.policy_sha256 != POLICY_SHA256:
        raise ValueError("predictions_policy_mismatch")
    prediction_ids = {prediction.fixture_id for prediction in parsed.predictions}
    if prediction_ids != fixture_ids:
        raise ValueError("predictions_fixture_set_mismatch")
    return parsed


def evaluate(
    manifest_path: Path,
    *,
    subset: Literal["ci", "full"] = "full",
    predictions_path: Path | None = None,
) -> dict:
    manifest = load_corpus_manifest(manifest_path)
    root = manifest_path.parent
    selected_ids = (
        set(manifest.ci_subset)
        if subset == "ci"
        else {fixture.id for fixture in manifest.fixtures}
    )
    selected = [fixture for fixture in manifest.fixtures if fixture.id in selected_ids]
    corpus_sha256 = _corpus_sha256(manifest)
    predictions = None
    if predictions_path is not None:
        predictions = _load_predictions(
            predictions_path,
            corpus_sha256=corpus_sha256,
            fixture_ids=selected_ids,
        )
    prediction_by_id = (
        {item.fixture_id: item.disposition for item in predictions.predictions}
        if predictions is not None
        else {}
    )
    evaluation_source = (
        {
            "kind": "frozen_predictions",
            "model": predictions.model,
            "prediction_set_sha256": hashlib.sha256(
                _canonical(predictions.model_dump(mode="json"))
            ).hexdigest(),
            "provider": predictions.provider,
        }
        if predictions is not None
        else {"kind": "deterministic_policy"}
    )

    mismatches = []
    eligible_false_positives = 0
    counts = {"eligible": 0, "human_review": 0, "unsupported": 0}
    resolved_root = root.resolve()
    for fixture in selected:
        path = root / fixture.path
        try:
            resolved_path = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"fixture_unavailable:{fixture.id}") from exc
        if not resolved_path.is_relative_to(resolved_root):
            raise ValueError(f"fixture_path_escape:{fixture.id}")
        payload = resolved_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != fixture.sha256:
            raise ValueError(f"fixture_digest_mismatch:{fixture.id}")
        actual = (
            prediction_by_id[fixture.id]
            if predictions is not None
            else classify_handwritten_math_suitability(payload).disposition
        )
        counts[actual] += 1
        if actual != fixture.expected_disposition:
            mismatches.append(
                {
                    "actual": actual,
                    "expected": fixture.expected_disposition,
                    "fixture_id": fixture.id,
                }
            )
        if (
            fixture.category in {"non_math_handwriting", "visually_similar_non_math"}
            and actual == "eligible"
        ):
            eligible_false_positives += 1

    accuracy_ppm = round((len(selected) - len(mismatches)) * 1_000_000 / len(selected))
    passed = (
        accuracy_ppm >= manifest.metric_gates.expected_disposition_accuracy_min_ppm
        and eligible_false_positives
        <= manifest.metric_gates.eligible_false_positives_max
    )
    return {
        "corpus_sha256": corpus_sha256,
        "disposition_counts": counts,
        "eligible_false_positives": eligible_false_positives,
        "evaluation_source": evaluation_source,
        "expected_disposition_accuracy_ppm": accuracy_ppm,
        "mismatches": mismatches,
        "passed": passed,
        "policy_sha256": POLICY_SHA256,
        "schema_version": "handwritten-math-evaluation-v1",
        "subset": subset,
        "total": len(selected),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/fixtures/handwritten_math/manifest.json"),
    )
    parser.add_argument("--subset", choices=("ci", "full"), default="full")
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate(
            args.manifest,
            subset=args.subset,
            predictions_path=args.predictions,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(_canonical(report).decode("utf-8"))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
