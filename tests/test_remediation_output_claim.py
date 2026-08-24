"""Descriptor-bound remediation output ownership contracts."""

import copy
import gc
import hashlib
import os
import pickle
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.education.remediation.base import RemediationResult
from src.education.remediation.output_claim import DescriptorBoundOutputClaim


def _claim(path: Path) -> DescriptorBoundOutputClaim:
    return DescriptorBoundOutputClaim.from_path(
        path,
        display_path="published/remediated.pdf",
        mime="application/pdf",
    )


def _result() -> RemediationResult:
    return RemediationResult(
        original_file="source.pdf",
        output_file="published/remediated.pdf",
        document_type="pdf",
    )


def test_claim_records_metadata_and_streams_exact_bytes(tmp_path):
    payload = b"descriptor-bound exact bytes\x00\xff"
    source = tmp_path / "candidate.pdf"
    source.write_bytes(payload)

    with _claim(source) as claim:
        assert claim.filename == "candidate.pdf"
        assert claim.display_path == "published/remediated.pdf"
        assert claim.size == len(payload)
        assert claim.sha256 == hashlib.sha256(payload).hexdigest()
        assert claim.mime == "application/pdf"
        assert claim.closed is False
        assert not hasattr(claim, "fileno")

        with claim.open_stream() as first:
            assert first.read() == payload
            assert os.get_inheritable(first.fileno()) is False
        with claim.open_stream() as second:
            assert second.tell() == 0
            assert second.read() == payload

    assert claim.closed is True


def test_claim_owned_descriptor_is_read_only_regular_and_noninheritable(
    tmp_path, monkeypatch
):
    import src.education.remediation.output_claim as mod

    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"candidate")
    opened = []
    real_open = os.open

    def recording_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(mod.os, "open", recording_open)
    claim = _claim(source)

    assert len(opened) == 1
    assert os.get_inheritable(opened[0]) is False
    with pytest.raises(OSError):
        os.write(opened[0], b"mutate")
    claim.close()
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_claim_rejects_nonregular_source(tmp_path):
    with pytest.raises(ValueError, match="regular file"):
        DescriptorBoundOutputClaim.from_path(
            tmp_path,
            display_path="published/remediated.pdf",
            mime="application/pdf",
        )


def test_borrowed_stream_survives_owner_close_and_closes_independently(tmp_path):
    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"borrowed bytes")
    claim = _claim(source)

    with claim.open_stream() as stream:
        claim.close()
        assert stream.read() == b"borrowed bytes"
        assert stream.closed is False

    assert stream.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        with claim.open_stream():
            pass


def test_concurrent_borrowers_each_receive_exact_stream(tmp_path):
    payload = b"parallel descriptor reads" * 1024
    source = tmp_path / "candidate.pdf"
    source.write_bytes(payload)

    with _claim(source) as claim:

        def read_claim() -> bytes:
            with claim.open_stream() as stream:
                return stream.read()

        with ThreadPoolExecutor(max_workers=8) as executor:
            assert (
                list(executor.map(lambda _: read_claim(), range(32))) == [payload] * 32
            )


def test_close_is_idempotent_and_context_manager_closes(tmp_path):
    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"close once")
    claim = _claim(source)

    claim.close()
    claim.close()

    assert claim.closed is True


def test_claim_rejects_copy_deepcopy_and_pickle(tmp_path):
    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"one owner")

    with _claim(source) as claim:
        for operation in (
            lambda: copy.copy(claim),
            lambda: copy.deepcopy(claim),
            lambda: pickle.dumps(claim),
        ):
            with pytest.raises(TypeError, match="cannot be copied or pickled"):
                operation()


def test_finalizer_closes_leaked_owned_descriptor(tmp_path, monkeypatch):
    import src.education.remediation.output_claim as mod

    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"finalizer backstop")
    opened = []
    real_open = os.open

    def recording_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(mod.os, "open", recording_open)
    claim = _claim(source)
    descriptor = opened[0]

    del claim
    gc.collect()

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_remediation_result_claim_api_preserves_schema_and_dump(tmp_path):
    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"result-owned bytes")
    result = _result()
    baseline_dump = result.model_dump()
    baseline_schema = result.model_json_schema()
    claim = _claim(source)

    result.set_output_claim(claim)

    assert result.has_output_claim() is True
    assert result.model_dump() == baseline_dump
    assert result.model_json_schema() == baseline_schema
    assert "output_claim" not in result.model_dump()
    with result.open_output_stream() as stream:
        assert stream.read() == b"result-owned bytes"

    taken = result.take_output_claim()
    assert taken is claim
    assert result.has_output_claim() is False
    with pytest.raises(RuntimeError, match="no live output claim"):
        with result.open_output_stream():
            pass
    taken.close()


def test_remediation_result_exposes_only_safe_claim_metadata(tmp_path):
    payload = b"safe claim metadata"
    source = tmp_path / "candidate.pdf"
    source.write_bytes(payload)
    result = _result()
    baseline_dump = result.model_dump()
    result.set_output_claim(_claim(source))

    assert result.output_claim_metadata() == {
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "mime_type": "application/pdf",
        "filename": "candidate.pdf",
    }
    assert result.model_dump() == baseline_dump
    assert "descriptor" not in repr(result.output_claim_metadata()).lower()
    assert "path" not in repr(result.output_claim_metadata()).lower()

    result.close_output_claim()
    with pytest.raises(RuntimeError, match="no live output claim"):
        result.output_claim_metadata()


def test_remediation_result_close_is_idempotent_and_rejects_live_aliasing(tmp_path):
    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"single result owner")
    result = _result()
    claim = _claim(source)
    result.set_output_claim(claim)

    with pytest.raises(TypeError, match="live output claim"):
        result.model_copy()
    with pytest.raises(TypeError, match="live output claim"):
        pickle.dumps(result)

    result.close_output_claim()
    result.close_output_claim()

    assert claim.closed is True
    assert result.has_output_claim() is False
    assert result.model_copy().model_dump() == result.model_dump()
    assert pickle.loads(pickle.dumps(result)).model_dump() == result.model_dump()


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy])
def test_remediation_result_python_copy_rejects_live_claim_and_preserves_owner(
    tmp_path, operation
):
    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"single result owner")
    result = _result()
    claim = _claim(source)
    result.set_output_claim(claim)

    with pytest.raises(TypeError, match="live output claim"):
        operation(result)

    assert result.has_output_claim() is True
    assert claim.closed is False
    result.close_output_claim()


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy])
def test_remediation_result_python_copy_succeeds_without_claim(tmp_path, operation):
    source = tmp_path / "candidate.pdf"
    source.write_bytes(b"released result owner")
    result = _result()
    result.set_output_claim(_claim(source))
    result.close_output_claim()

    copied = operation(result)

    assert copied is not result
    assert copied.model_dump() == result.model_dump()
    assert copied.has_output_claim() is False


def test_remediation_result_rejects_replacing_live_claim(tmp_path):
    first_path = tmp_path / "first.pdf"
    second_path = tmp_path / "second.pdf"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    result = _result()
    first = _claim(first_path)
    second = _claim(second_path)

    result.set_output_claim(first)
    with pytest.raises(RuntimeError, match="already owns"):
        result.set_output_claim(second)

    result.close_output_claim()
    second.close()


def test_auto_remediator_path_facade_closes_live_claim(tmp_path, monkeypatch):
    import src.remediation.auto_remediator as mod

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    source.write_bytes(b"source")
    output.write_bytes(b"claimed output")
    result = _result()
    claim = DescriptorBoundOutputClaim.from_path(
        output,
        display_path=str(output),
        mime="application/pdf",
    )
    result.set_output_claim(claim)

    class FakeRemediator:
        def __init__(self, **kwargs):
            pass

        def remediate(self):
            return result

    monkeypatch.setattr(mod, "get_remediator_for_file", lambda path: FakeRemediator)

    response = mod.AutoRemediator().remediate(str(source))

    assert response["success"] is True
    assert response["output_path"] == result.output_file
    assert claim.closed is True
    assert result.has_output_claim() is False
