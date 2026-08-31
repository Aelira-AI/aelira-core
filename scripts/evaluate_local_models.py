"""Evaluate pinned Ollama models on public Aelira remediation fixtures.

The live evaluator is deliberately separate from ordinary CI. CI validates the
matrix, deterministic output validators, checked-in report, and documentation
contract without requiring Ollama. Run the live evaluation explicitly:

    python scripts/evaluate_local_models.py --output /tmp/local-model-results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import ollama
import psutil
import pymupdf
from bs4 import BeautifulSoup

from src.ai.lms_readiness import canonical_loopback_ollama_host
from src.ai.providers.types import OLLAMA_EVALUATED_MODELS

MATRIX_PATH = ROOT / "tests/fixtures/local_models/matrix.json"
REPORT_PATH = ROOT / "docs/deployment/local-ai-model-results.json"
DOCUMENTATION_PATH = ROOT / "docs/deployment/local-ai-models.md"
EVALUATOR_VERSION = "1.0.0"
SUPPORTED_MODELS = OLLAMA_EVALUATED_MODELS

TEXT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["issue", "recommendation", "review_required"],
    "properties": {
        "issue": {"type": "string"},
        "recommendation": {"type": "string"},
        "review_required": {"type": "boolean"},
    },
}
CODE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["html"],
    "properties": {"html": {"type": "string"}},
}
CHART_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "series"],
    "properties": {
        "title": {"type": "string"},
        "series": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "value"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "number"},
                },
            },
        },
    },
}
SCANNED_DOCUMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["course", "term", "instructor", "office", "office_hours"],
    "properties": {
        "course": {"type": "string"},
        "term": {"type": "string"},
        "instructor": {"type": "string"},
        "office": {"type": "string"},
        "office_hours": {"type": "string"},
    },
}


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    code: str
    observations: dict[str, Any]


def _result(
    passed: bool, code: str, observations: Mapping[str, Any] | None = None
) -> ValidationResult:
    return ValidationResult(passed, code, dict(observations or {}))


def _payload(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def validate_text_response(value: object) -> ValidationResult:
    payload = _payload(value)
    if payload is None:
        return _result(False, "malformed_response")
    issue = str(payload.get("issue", "")).lower()
    recommendation = str(payload.get("recommendation", "")).lower()
    if "alt" not in issue and "alternative text" not in issue:
        return _result(False, "alt_text_issue_missing")
    if "alt" not in recommendation and "alternative text" not in recommendation:
        return _result(False, "alt_text_fix_missing")
    if payload.get("review_required") is not True:
        return _result(False, "human_review_boundary_missing")
    return _result(
        True,
        "passed",
        {
            "identified_missing_alt_text": True,
            "recommended_alt_attribute": True,
            "human_review_required": True,
        },
    )


def validate_code_response(value: object) -> ValidationResult:
    payload = _payload(value)
    if payload is None or not isinstance(payload.get("html"), str):
        return _result(False, "malformed_response")
    soup = BeautifulSoup(payload["html"], "html.parser")
    inputs = soup.find_all("input")
    submit = soup.find("button", attrs={"type": "submit"})
    if len(inputs) != 2 or submit is None:
        return _result(False, "form_controls_missing")
    input_ids = [item.get("id") for item in inputs]
    if any(not item for item in input_ids) or len(set(input_ids)) != len(input_ids):
        return _result(False, "input_id_missing")
    labels = {label.get("for") for label in soup.find_all("label") if label.get("for")}
    if any(item not in labels for item in input_ids):
        return _result(False, "input_label_missing")
    return _result(
        True,
        "passed",
        {
            "inputs": len(inputs),
            "labelled_inputs": len(input_ids),
            "submit_button_preserved": True,
        },
    )


def validate_chart_response(value: object) -> ValidationResult:
    payload = _payload(value)
    if payload is None or not isinstance(payload.get("series"), list):
        return _result(False, "malformed_response")
    title = str(payload.get("title", "")).lower()
    if "quarterly" not in title or "revenue" not in title:
        return _result(False, "chart_title_incorrect")
    observed: dict[str, float] = {}
    for item in payload["series"]:
        if not isinstance(item, dict):
            return _result(False, "malformed_response")
        try:
            observed[str(item["label"]).upper()] = float(item["value"])
        except (KeyError, TypeError, ValueError):
            return _result(False, "malformed_response")
    expected = {"Q1": 65.0, "Q2": 80.0, "Q3": 55.0, "Q4": 90.0}
    if set(observed) != set(expected):
        return _result(False, "chart_labels_incorrect", {"labels": sorted(observed)})
    if any(abs(observed[key] - expected[key]) > 0.5 for key in expected):
        return _result(False, "chart_values_incorrect", {"series": observed})
    return _result(True, "passed", {"title": payload["title"], "series": observed})


def _normalized(value: object) -> str:
    return "".join(character.lower() for character in str(value) if character.isalnum())


def validate_scanned_document_response(value: object) -> ValidationResult:
    payload = _payload(value)
    if payload is None:
        return _result(False, "malformed_response")
    normalized = {
        key: _normalized(payload.get(key, ""))
        for key in SCANNED_DOCUMENT_SCHEMA["required"]
    }
    required_fragments = {
        "course": ("math250", "introductiontostatistics"),
        "term": ("fall2025",),
        "instructor": ("emilychen",),
        "office": ("mathematicsbuilding", "312"),
        "office_hours": ("tuesday", "thursday", "200", "400"),
    }
    if any(
        any(fragment not in normalized[field] for fragment in fragments)
        for field, fragments in required_fragments.items()
    ):
        return _result(False, "document_fields_incorrect", normalized)
    return _result(True, "passed", normalized)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embedding vectors must have equal nonzero dimensions")
    numerator = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("embedding vectors must have nonzero norms")
    return numerator / (left_norm * right_norm)


def validate_embedding_vectors(value: object) -> ValidationResult:
    if not isinstance(value, dict):
        return _result(False, "malformed_response")
    try:
        anchor = value["anchor"]
        identical = cosine_similarity(anchor, value["identical"])
        related = cosine_similarity(anchor, value["related"])
        unrelated = cosine_similarity(anchor, value["unrelated"])
    except (KeyError, TypeError, ValueError):
        return _result(False, "malformed_response")
    observations = {
        "dimensions": len(anchor),
        "identical_similarity": round(identical, 6),
        "related_similarity": round(related, 6),
        "unrelated_similarity": round(unrelated, 6),
    }
    if identical < 0.999 or related <= unrelated + 0.05:
        return _result(False, "embedding_ranking_failed", observations)
    return _result(True, "passed", observations)


def summarize_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, int | float]:
    elapsed = [float(run["elapsed_ms"]) for run in runs]
    if not elapsed:
        raise ValueError("at least one run is required")
    return {
        "median_elapsed_ms": round(statistics.median(elapsed), 3),
        "max_elapsed_ms": round(max(elapsed), 3),
        "passed_runs": sum(run.get("passed") is True for run in runs),
        "total_runs": len(runs),
    }


def load_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def _tracked_paths(repo_root: Path) -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line for line in completed.stdout.splitlines() if line}


def validate_matrix(matrix: Mapping[str, Any], repo_root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if matrix.get("schema_version") != 1:
        errors.append("matrix schema_version must be 1")
    repeat_count = matrix.get("repeat_count")
    if (
        isinstance(repeat_count, bool)
        or not isinstance(repeat_count, int)
        or repeat_count < 2
    ):
        errors.append("matrix repeat_count must be an integer of at least 2")
    raw_models = matrix.get("models")
    if not isinstance(raw_models, list):
        return errors + ["matrix models must be a list"]
    observed_models = {
        str(entry.get("lane")): str(entry.get("model"))
        for entry in raw_models
        if isinstance(entry, dict)
    }
    if observed_models != SUPPORTED_MODELS:
        errors.append("matrix must contain the exact pinned lane/model pairs")
    case_ids: set[str] = set()
    tracked = _tracked_paths(repo_root)
    allowed_roots = (
        (repo_root / "tests/fixtures").resolve(),
        MATRIX_PATH.parent.resolve(),
    )
    for entry in raw_models:
        if not isinstance(entry, dict) or not isinstance(entry.get("cases"), list):
            errors.append("each model entry must contain cases")
            continue
        if not entry["cases"]:
            errors.append(f"lane {entry.get('lane')} must contain at least one case")
        for case in entry["cases"]:
            if not isinstance(case, dict) or not isinstance(case.get("id"), str):
                errors.append("each case must contain a string id")
                continue
            if case["id"] in case_ids:
                errors.append(f"duplicate case id: {case['id']}")
            case_ids.add(case["id"])
            fixture = case.get("fixture")
            if fixture is None:
                if case.get("kind") != "embeddings" or not isinstance(
                    case.get("texts"), dict
                ):
                    errors.append(
                        f"case {case['id']} has no fixture or embedding texts"
                    )
                continue
            if not isinstance(fixture, str):
                errors.append(f"case {case['id']} fixture must be a string")
                continue
            resolved = (repo_root / fixture).resolve()
            if not any(resolved.is_relative_to(root) for root in allowed_roots):
                errors.append(
                    f"case {case['id']} fixture is outside public fixture roots"
                )
            if not resolved.is_file():
                errors.append(f"case {case['id']} fixture does not exist: {fixture}")
            if fixture not in tracked:
                errors.append(f"case {case['id']} fixture is not tracked: {fixture}")
    return errors


def create_client(host: str, timeout: float) -> ollama.Client:
    canonical = canonical_loopback_ollama_host(host)
    if canonical is None:
        raise ValueError("Ollama evaluator host must be an explicit loopback URL")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite number")
    if not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise ValueError("timeout must be between 1 and 600 seconds")
    return ollama.Client(
        host=canonical,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _render_pdf_page(path: Path, page_number: int, dpi: int) -> bytes:
    if page_number < 1 or dpi < 72 or dpi > 300:
        raise ValueError("PDF page and render DPI are outside evaluator bounds")
    with pymupdf.open(path) as document:
        page = document.load_page(page_number - 1)
        scale = dpi / 72
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")


def _case_material(case: Mapping[str, Any]) -> dict[str, Any]:
    fixture = case.get("fixture")
    if fixture is None:
        texts = dict(case["texts"])
        canonical = _json_bytes(texts)
        return {
            "source_path": str(MATRIX_PATH.relative_to(ROOT)),
            "source_sha256": sha256_bytes(MATRIX_PATH.read_bytes()),
            "input_sha256": sha256_bytes(canonical),
            "texts": texts,
        }
    fixture_path = (ROOT / str(fixture)).resolve()
    source_bytes = fixture_path.read_bytes()
    if case["kind"] == "scanned_document":
        input_bytes = _render_pdf_page(
            fixture_path, int(case["page"]), int(case["render_dpi"])
        )
    else:
        input_bytes = source_bytes
    return {
        "source_path": str(fixture),
        "source_sha256": sha256_bytes(source_bytes),
        "input_sha256": sha256_bytes(input_bytes),
        "input_bytes": input_bytes,
    }


def _generation_request(kind: str, material: Mapping[str, Any]) -> dict[str, Any]:
    if kind == "text":
        html = material["input_bytes"].decode("utf-8")
        return {
            "prompt": (
                "Review this real accessibility fixture. Identify the first image's "
                "missing-alt-text issue, recommend a safe fix, and require human review "
                "because the file contains no evidence of the image's purpose. Do not invent "
                f"alt text.\n\n{html}"
            ),
            "format": TEXT_SCHEMA,
            "validator": validate_text_response,
        }
    if kind == "code":
        html = material["input_bytes"].decode("utf-8")
        return {
            "prompt": (
                "Repair the form in this real fixture. Return complete HTML with an explicit "
                "label associated by for/id with each input. Preserve both input types and the "
                f"submit button.\n\n{html}"
            ),
            "format": CODE_SCHEMA,
            "validator": validate_code_response,
        }
    if kind == "chart":
        return {
            "prompt": (
                "Read the chart pixels. Return its exact title and all four quarter labels with "
                "their numeric values. Do not infer values that are not visible."
            ),
            "format": CHART_SCHEMA,
            "images": [material["input_bytes"]],
            "validator": validate_chart_response,
        }
    if kind == "scanned_document":
        return {
            "prompt": (
                "Treat this rasterized document page as a scanned-page remediation input. "
                "Read only visible pixels and return the course, term, instructor, office, "
                "and office hours exactly enough to verify them."
            ),
            "format": SCANNED_DOCUMENT_SCHEMA,
            "images": [material["input_bytes"]],
            "validator": validate_scanned_document_response,
        }
    raise ValueError(f"unsupported generation case kind: {kind}")


def _response_value(response: object, name: str, default: Any = None) -> Any:
    if hasattr(response, name):
        return getattr(response, name)
    if isinstance(response, dict):
        return response.get(name, default)
    return default


def _live_generation_run(
    client: ollama.Client,
    model: str,
    case: Mapping[str, Any],
    material: Mapping[str, Any],
) -> dict[str, Any]:
    request = _generation_request(str(case["kind"]), material)
    started = time.perf_counter()
    try:
        response = client.generate(
            model=model,
            prompt=request["prompt"],
            format=request["format"],
            images=request.get("images"),
            options={"temperature": 0, "num_predict": 512, "seed": 214},
            keep_alive="5m",
        )
    except httpx.TimeoutException:
        return _failed_run(started, "request_timeout")
    except Exception as error:
        code = (
            "model_missing" if "not found" in str(error).lower() else "request_failed"
        )
        return _failed_run(started, code)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    validation = request["validator"](_response_value(response, "response", ""))
    eval_count = int(_response_value(response, "eval_count", 0) or 0)
    eval_duration = int(_response_value(response, "eval_duration", 0) or 0)
    tokens_per_second = (
        round(eval_count / (eval_duration / 1_000_000_000), 3)
        if eval_count and eval_duration
        else 0.0
    )
    return {
        "elapsed_ms": elapsed_ms,
        "passed": validation.passed,
        "code": validation.code,
        "observations": validation.observations,
        "eval_tokens": eval_count,
        "tokens_per_second": tokens_per_second,
    }


def _live_embedding_run(
    client: ollama.Client, model: str, material: Mapping[str, Any]
) -> dict[str, Any]:
    texts = material["texts"]
    ordered_names = ("anchor", "identical", "related", "unrelated")
    started = time.perf_counter()
    try:
        response = client.embed(
            model=model,
            input=[texts[name] for name in ordered_names],
            keep_alive="5m",
        )
    except httpx.TimeoutException:
        return _failed_run(started, "request_timeout")
    except Exception as error:
        code = (
            "model_missing" if "not found" in str(error).lower() else "request_failed"
        )
        return _failed_run(started, code)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    raw_vectors = _response_value(response, "embeddings", [])
    vectors = (
        dict(zip(ordered_names, raw_vectors))
        if isinstance(raw_vectors, list) and len(raw_vectors) == len(ordered_names)
        else {}
    )
    validation = validate_embedding_vectors(vectors)
    return {
        "elapsed_ms": elapsed_ms,
        "passed": validation.passed,
        "code": validation.code,
        "observations": validation.observations,
        "eval_tokens": 0,
        "tokens_per_second": 0.0,
    }


def _failed_run(started: float, code: str) -> dict[str, Any]:
    return {
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "passed": False,
        "code": code,
        "observations": {},
        "eval_tokens": 0,
        "tokens_per_second": 0.0,
    }


def classify_support(cases: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for case in cases:
        grouped.setdefault(str(case["lane"]), []).append(case)
    support: dict[str, dict[str, Any]] = {}
    for lane, lane_cases in sorted(grouped.items()):
        runs = [run for case in lane_cases for run in case.get("runs", [])]
        passed = bool(runs) and all(run.get("passed") is True for run in runs)
        failures = sorted(
            {
                str(run.get("code", "request_failed"))
                for run in runs
                if run.get("passed") is not True
            }
        )
        support[lane] = {
            "model": str(lane_cases[0]["model"]),
            "status": "supported" if passed else "unsupported",
            "cases": [str(case["case_id"]) for case in lane_cases],
            "failure_modes": failures,
        }
    return support


def _model_objects(response: object) -> list[Any]:
    raw = _response_value(response, "models", [])
    return list(raw) if isinstance(raw, (list, tuple)) else []


def _model_name(item: object) -> str:
    return str(
        _response_value(item, "model", None)
        or _response_value(item, "name", None)
        or ""
    )


def _model_evidence(client: ollama.Client, model: str) -> dict[str, Any]:
    listed = next(
        (item for item in _model_objects(client.list()) if _model_name(item) == model),
        None,
    )
    running = next(
        (item for item in _model_objects(client.ps()) if _model_name(item) == model),
        None,
    )
    download_bytes = int(_response_value(listed, "size", 0) or 0)
    loaded_bytes = int(_response_value(running, "size", 0) or 0)
    gpu_bytes = int(_response_value(running, "size_vram", 0) or 0)
    gpu_percent = round((gpu_bytes / loaded_bytes) * 100, 1) if loaded_bytes else 0.0
    return {
        "tag": model,
        "model_id": str(_response_value(listed, "digest", "")),
        "download_bytes": download_bytes,
        "loaded_bytes": loaded_bytes,
        "processor": {
            "cpu_percent": round(100.0 - gpu_percent, 1) if loaded_bytes else 0.0,
            "gpu_percent": gpu_percent,
        },
    }


def _host_evidence() -> dict[str, Any]:
    chip = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ["system_profiler", "SPHardwareDataType", "-json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            hardware = json.loads(completed.stdout)["SPHardwareDataType"][0]
            chip = str(hardware.get("chip_type") or chip)
        except (KeyError, ValueError, subprocess.SubprocessError):
            pass
    try:
        version = subprocess.run(
            ["ollama", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except subprocess.SubprocessError:
        version = "unavailable"
    return {
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "chip": chip,
        "memory_bytes": int(psutil.virtual_memory().total),
        "ollama_version": version,
    }


def evaluate(
    matrix: Mapping[str, Any],
    host: str,
    timeout: float,
    repeat_count: int | None = None,
) -> dict[str, Any]:
    errors = validate_matrix(matrix)
    if errors:
        raise ValueError("; ".join(errors))
    client = create_client(host, timeout)
    repeats = repeat_count or int(matrix["repeat_count"])
    if repeats < 2 or repeats > 10:
        raise ValueError("repeat count must be between 2 and 10")
    cases: list[dict[str, Any]] = []
    model_evidence: dict[str, dict[str, Any]] = {}
    try:
        available = {_model_name(item) for item in _model_objects(client.list())}
        for entry in matrix["models"]:
            lane = str(entry["lane"])
            model = str(entry["model"])
            for case in entry["cases"]:
                material = _case_material(case)
                runs: list[dict[str, Any]] = []
                for index in range(repeats):
                    if model not in available:
                        run = {
                            "elapsed_ms": 0.0,
                            "passed": False,
                            "code": "model_missing",
                            "observations": {},
                            "eval_tokens": 0,
                            "tokens_per_second": 0.0,
                        }
                    elif case["kind"] == "embeddings":
                        run = _live_embedding_run(client, model, material)
                    else:
                        run = _live_generation_run(client, model, case, material)
                    run["run"] = index + 1
                    runs.append(run)
                cases.append(
                    {
                        "lane": lane,
                        "model": model,
                        "case_id": str(case["id"]),
                        "kind": str(case["kind"]),
                        "source": {
                            "path": material["source_path"],
                            "sha256": material["source_sha256"],
                        },
                        "input_sha256": material["input_sha256"],
                        "runs": runs,
                        "summary": summarize_runs(runs),
                    }
                )
            if model in available:
                model_evidence[lane] = _model_evidence(client, model)
            else:
                model_evidence[lane] = {
                    "tag": model,
                    "model_id": "",
                    "download_bytes": 0,
                    "loaded_bytes": 0,
                    "processor": {"cpu_percent": 0.0, "gpu_percent": 0.0},
                }
        report = {
            "schema_version": 1,
            "evaluator_version": EVALUATOR_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "matrix_sha256": sha256_bytes(_json_bytes(matrix)),
            "endpoint": canonical_loopback_ollama_host(host),
            "host": _host_evidence(),
            "models": model_evidence,
            "cases": cases,
            "support": classify_support(cases),
        }
        return report
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def validate_report(report: Mapping[str, Any], matrix: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != 1:
        errors.append("report schema_version must be 1")
    if report.get("evaluator_version") != EVALUATOR_VERSION:
        errors.append("report evaluator_version is stale")
    if report.get("matrix_sha256") != sha256_bytes(_json_bytes(matrix)):
        errors.append("report matrix digest is stale")
    if canonical_loopback_ollama_host(report.get("endpoint")) is None:
        errors.append("report endpoint is not canonical loopback")
    host = report.get("host")
    if not isinstance(host, dict) or not all(
        host.get(field)
        for field in (
            "platform",
            "architecture",
            "chip",
            "memory_bytes",
            "ollama_version",
        )
    ):
        errors.append("report host evidence is incomplete")
    expected_cases = {
        (entry["lane"], entry["model"], case["id"])
        for entry in matrix["models"]
        for case in entry["cases"]
    }
    raw_cases = report.get("cases")
    if not isinstance(raw_cases, list):
        return errors + ["report cases must be a list"]
    observed_cases = {
        (case.get("lane"), case.get("model"), case.get("case_id"))
        for case in raw_cases
        if isinstance(case, dict)
    }
    if observed_cases != expected_cases:
        errors.append("report cases do not match the matrix")
    repeats = int(matrix["repeat_count"])
    for case in raw_cases:
        if not isinstance(case, dict):
            errors.append("report case must be an object")
            continue
        source = case.get("source")
        if not isinstance(source, dict) or len(str(source.get("sha256", ""))) != 64:
            errors.append(f"case {case.get('case_id')} source digest is missing")
        if len(str(case.get("input_sha256", ""))) != 64:
            errors.append(f"case {case.get('case_id')} input digest is missing")
        runs = case.get("runs")
        if not isinstance(runs, list) or len(runs) != repeats:
            errors.append(f"case {case.get('case_id')} run count is incorrect")
            continue
        try:
            if case.get("summary") != summarize_runs(runs):
                errors.append(f"case {case.get('case_id')} summary is stale")
        except (KeyError, TypeError, ValueError):
            errors.append(f"case {case.get('case_id')} runs are malformed")
    models = report.get("models")
    if not isinstance(models, dict) or set(models) != set(SUPPORTED_MODELS):
        errors.append("report model evidence does not cover every lane")
    else:
        for lane, model in SUPPORTED_MODELS.items():
            evidence = models[lane]
            if evidence.get("tag") != model or not evidence.get("model_id"):
                errors.append(f"model evidence is incomplete for {lane}")
            if not all(
                isinstance(evidence.get(field), int) and evidence[field] > 0
                for field in ("download_bytes", "loaded_bytes")
            ):
                errors.append(f"model size evidence is incomplete for {lane}")
            processor = evidence.get("processor", {})
            if (
                round(
                    float(processor.get("cpu_percent", -1))
                    + float(processor.get("gpu_percent", -1)),
                    1,
                )
                != 100.0
            ):
                errors.append(f"processor split is incomplete for {lane}")
    expected_support = classify_support(raw_cases)
    if report.get("support") != expected_support:
        errors.append("report support classification is stale")
    return errors


def _human_bytes(value: int) -> str:
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or suffix == "TiB":
            return f"{value:.1f} {suffix}" if suffix != "B" else f"{value} B"
        value /= 1024
    raise AssertionError("unreachable")


def evidence_block(report: Mapping[str, Any]) -> str:
    host = report["host"]
    lines = [
        "<!-- local-model-evidence:start -->",
        "## Reproduced support matrix",
        "",
        "The checked-in [machine-readable result](local-ai-model-results.json) was "
        f"produced by evaluator `{report['evaluator_version']}` on "
        f"{host['chip']} with {_human_bytes(int(host['memory_bytes']))} memory. "
        "These measurements describe that host only; they are not universal hardware claims.",
        "",
        "| Lane | Exact model | Status | Fixture cases | Median latency | Maximum latency | Download / loaded | Processor |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lane in ("vision", "text", "code", "embeddings"):
        support = report["support"][lane]
        cases = [case for case in report["cases"] if case["lane"] == lane]
        medians = [float(case["summary"]["median_elapsed_ms"]) for case in cases]
        maximums = [float(case["summary"]["max_elapsed_ms"]) for case in cases]
        evidence = report["models"][lane]
        status = "Supported" if support["status"] == "supported" else "Unsupported"
        processor = evidence["processor"]
        median_latency = (
            f"{medians[0] / 1000:.2f} s"
            if len(medians) == 1
            else f"{min(medians) / 1000:.2f}-{max(medians) / 1000:.2f} s"
        )
        maximum_latency = (
            f"{maximums[0] / 1000:.2f} s"
            if len(maximums) == 1
            else f"{min(maximums) / 1000:.2f}-{max(maximums) / 1000:.2f} s"
        )
        lines.append(
            f"| {lane.title()} | `{support['model']}` | **{status}** | "
            f"{', '.join(support['cases'])} | {median_latency} | {maximum_latency} | "
            f"{_human_bytes(int(evidence['download_bytes']))} / "
            f"{_human_bytes(int(evidence['loaded_bytes']))} | "
            f"{processor['cpu_percent']:.1f}% CPU / {processor['gpu_percent']:.1f}% GPU |"
        )
    lines.extend(
        [
            "",
            "Supported means the exact tag and model ID passed every required fixture "
            "run for that lane. Other Ollama tags remain configurable and API-compatible, "
            "but unverified by this release. Missing, timed-out, malformed, or validator-"
            "failing runs remain unsupported rather than falling back to a claim.",
            "<!-- local-model-evidence:end -->",
        ]
    )
    return "\n".join(lines)


def verify_documentation_contract(
    documentation: str, report: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    expected_block = evidence_block(report)
    start = "<!-- local-model-evidence:start -->"
    end = "<!-- local-model-evidence:end -->"
    if start not in documentation or end not in documentation:
        return ["documentation evidence block is missing"]
    actual = documentation.split(start, 1)[1].split(end, 1)[0]
    expected = expected_block.split(start, 1)[1].split(end, 1)[0]
    if actual.strip() != expected.strip():
        errors.append("documentation evidence block is stale")
    if "Any Ollama model" in documentation:
        errors.append("documentation describes unverified Ollama tags as supported")
    if "LLM_PROVIDER=ollama" not in documentation:
        errors.append("documentation no longer requires explicit Ollama selection")
    defaults_section = documentation.split("## Evaluated defaults", 1)
    if len(defaults_section) != 2:
        errors.append("documentation evaluated-defaults section is missing")
    else:
        defaults_body = defaults_section[1].split(start, 1)[0]
        documented_models = {
            match.group(1).lower(): match.group(2)
            for match in re.finditer(
                r"^\| (Vision|Text|Code|Embeddings) \| `([^`]+)` \|",
                defaults_body,
                flags=re.MULTILINE,
            )
        }
        expected_models = {
            lane: details["model"] for lane, details in report["support"].items()
        }
        if documented_models != expected_models:
            errors.append("documentation evaluated defaults do not match the report")
    return errors


def replace_evidence_block(documentation: str, report: Mapping[str, Any]) -> str:
    start = "<!-- local-model-evidence:start -->"
    end = "<!-- local-model-evidence:end -->"
    block = evidence_block(report)
    if start not in documentation or end not in documentation:
        marker = "## Hardware tiers"
        if marker not in documentation:
            raise ValueError(
                "local AI guide has no evidence block or hardware tiers marker"
            )
        return documentation.replace(marker, f"{block}\n\n{marker}", 1)
    prefix = documentation.split(start, 1)[0]
    suffix = documentation.split(end, 1)[1]
    return f"{prefix}{block}{suffix}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--update-docs", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    matrix = load_matrix()
    if args.check:
        report = json.loads(args.output.read_text())
        errors = validate_matrix(matrix) + validate_report(report, matrix)
        errors += verify_documentation_contract(DOCUMENTATION_PATH.read_text(), report)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("Local model evidence contract verified")
        return 0
    report = evaluate(matrix, args.host, args.timeout, args.repeat)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.update_docs:
        DOCUMENTATION_PATH.write_text(
            replace_evidence_block(DOCUMENTATION_PATH.read_text(), report)
        )
    failures = [
        lane
        for lane, result in report["support"].items()
        if result["status"] != "supported"
    ]
    print(f"Wrote {args.output}")
    if failures:
        print(f"Unsupported lanes: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("All pinned local-model lanes passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
