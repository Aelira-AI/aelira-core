"""Measurement receipts bind finite scanner scores to stable artifact bytes."""

import hashlib

import pytest

from src.education.remediation.score_measurement import (
    METHOD_VERSIONS,
    REASON_CODES,
    MeasurementError,
    begin_measurement,
    finish_measurement,
    valid_measurement,
)
from src.education.remediation.score_reporting import score_fields


@pytest.fixture
def documents(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    source.write_bytes(b"original fixture bytes")
    output.write_bytes(b"output fixture bytes")
    return source, output


def receipt():
    return {
        "method_version": "pdf-strict-v1",
        "source_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "source_score": 95.7,
        "output_score": 98,
    }


def test_begin_and_finish_measure_the_exact_saved_bytes(documents):
    source, output = documents
    snapshot = begin_measurement(source, output)
    assert snapshot == {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    result = finish_measurement(snapshot, source, output, 95.7, 98, "pdf-strict-v1")
    assert result == {
        **snapshot,
        "method_version": "pdf-strict-v1",
        "source_score": 95.7,
        "output_score": 98,
    }


@pytest.mark.parametrize("changed_index", [0, 1])
def test_file_changed_during_scan_is_artifact_mismatch(documents, changed_index):
    snapshot = begin_measurement(*documents)
    documents[changed_index].write_bytes(b"different bytes after scanner started")
    with pytest.raises(MeasurementError) as error:
        finish_measurement(snapshot, *documents, 95.7, 98, "pdf-strict-v1")
    assert error.value.code == "artifact_mismatch"
    assert str(error.value) == "artifact_mismatch"


@pytest.mark.parametrize(
    "index,code", [(0, "original_file_missing"), (1, "output_file_missing")]
)
def test_missing_file_before_and_after_scan_has_safe_reason(documents, index, code):
    snapshot = begin_measurement(*documents)
    documents[index].unlink()
    for operation in [
        lambda: begin_measurement(*documents),
        lambda: finish_measurement(snapshot, *documents, 95.7, 98, "pdf-strict-v1"),
    ]:
        with pytest.raises(MeasurementError) as error:
            operation()
        assert error.value.code == code
        assert str(error.value) == code


@pytest.mark.parametrize("method", sorted(METHOD_VERSIONS))
def test_all_supported_methods_produce_valid_receipts(method):
    assert (
        valid_measurement({**receipt(), "method_version": method})["method_version"]
        == method
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("method_version", "unknown-v1"),
        ("method_version", None),
        ("method_version", []),
        ("method_version", {"version": "pdf-strict-v1"}),
        ("source_sha256", "a" * 63),
        ("output_sha256", "B" * 64),
        ("source_sha256", "g" * 64),
        ("output_sha256", 123),
        ("source_score", True),
        ("output_score", "98"),
        ("source_score", float("nan")),
        ("output_score", float("inf")),
        ("source_score", float("-inf")),
        ("output_score", -1),
        ("source_score", 101),
        ("output_score", None),
    ],
)
def test_malformed_receipts_are_rejected(key, value):
    assert valid_measurement({**receipt(), key: value}) is None


@pytest.mark.parametrize("value", [None, [], "receipt", 5, True, {}])
def test_non_receipts_are_rejected(value):
    assert valid_measurement(value) is None


def test_validation_strips_unknown_fields_and_preserves_zero():
    source = {
        **receipt(),
        "source_score": 0,
        "output_score": 100,
        "extra": "unrecognized detail",
    }
    result = valid_measurement(source)
    assert result == {**receipt(), "source_score": 0, "output_score": 100}
    assert source["extra"] == "unrecognized detail"


@pytest.mark.parametrize(
    "before,after,method",
    [
        (None, 98, "pdf-strict-v1"),
        (95.7, float("nan"), "pdf-strict-v1"),
        (95.7, 98, "unknown-v1"),
    ],
)
def test_finish_rejects_invalid_scores_and_method(documents, before, after, method):
    with pytest.raises(MeasurementError) as error:
        finish_measurement(
            begin_measurement(*documents), *documents, before, after, method
        )
    assert error.value.code == "incomplete_comparison"


@pytest.mark.parametrize("code", sorted(REASON_CODES))
def test_all_nine_codes_remain_safe_across_measurement_and_reporting(code):
    assert len(REASON_CODES) == 9
    assert str(MeasurementError(code)) == code
    result = {
        "score_provenance": "scanner_rescan",
        "original_compliance_score": 95.7,
        "remediated_compliance_score": 98,
        "score_measurement": receipt(),
        "score_verification_reason": code,
    }
    fields = score_fields(result, original_score=95.7)
    assert fields["score_verification_reason"] == code
    assert fields["score_verified"] is False
    assert fields["remediated_compliance_score"] is None


@pytest.mark.parametrize(
    "code",
    ["unexpected diagnostic detail", "", None, 42, [], {"code": "output_scan_failed"}],
)
def test_unknown_measurement_error_never_echoes_raw_detail(code):
    error = MeasurementError(code)
    assert error.code == "incomplete_comparison"
    assert str(error) == "incomplete_comparison"
