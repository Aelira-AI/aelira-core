"""Regression guards for descriptor-bound Canvas managed-artifact writeback."""

import inspect
import io
from types import SimpleNamespace

import httpx
import pytest

from src.education.canvas_content_scanner import CanvasContentScanner
from src.integrations.canvas import canvas_api
from src.integrations.canvas.canvas_api import CanvasAPIClient


class _UploadClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.cookies = SimpleNamespace(clear=lambda: None)
        self.post_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        self.post_calls += 1
        return self.response


class _PreacceptClient:
    async def post(self, *_args, **_kwargs):
        request = httpx.Request("POST", "https://canvas.example/api/v1/courses/1/files")
        return httpx.Response(
            200,
            request=request,
            json={
                "upload_url": "https://canvas.example/upload",
                "upload_params": {"key": "value"},
            },
        )


async def _direct_upload_result(monkeypatch, status_code: int):
    monkeypatch.setattr(
        canvas_api, "resolve_canvas_network_origin", lambda url: url.rstrip("/")
    )
    monkeypatch.setattr(
        canvas_api,
        "prepare_canvas_outbound_url",
        lambda url, _base, **_kwargs: url,
    )
    client = CanvasAPIClient("https://canvas.example", "secret")
    monkeypatch.setattr(client, "_client", _PreacceptClient())
    request = httpx.Request("POST", "https://canvas.example/upload")
    response_body = (
        {"id": 77, "filename": "fixed.pdf", "url": "https://canvas.example/files/77"}
        if status_code < 400
        else {"errors": [{"message": "rejected"}]}
    )
    upload_client = _UploadClient(
        httpx.Response(status_code, request=request, json=response_body)
    )
    monkeypatch.setattr(
        canvas_api.httpx, "AsyncClient", lambda **_kwargs: upload_client
    )

    result = await client.upload_file_stream(
        course_id="1",
        stream=io.BytesIO(b"fixed"),
        size_bytes=5,
        mime_type="application/pdf",
        file_name="fixed.pdf",
        correlation_id="11111111-1111-4111-8111-111111111111",
    )
    return result, upload_client


def test_job_json_path_discovery_was_removed():
    source = inspect.getsource(CanvasContentScanner.write_back_file)
    assert "_find_remediated_file_path" not in source
    assert "result_data" not in source
    assert "local_path" not in source
    assert "open_verified" in source
    assert "upload_file_stream" in source


def test_canvas_has_descriptor_stream_adapter():
    signature = inspect.signature(CanvasAPIClient.upload_file_stream)
    assert "stream" in signature.parameters
    assert "size_bytes" in signature.parameters
    assert "mime_type" in signature.parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 502, 503, 408, 429])
async def test_canvas_post_body_retry_unsafe_status_is_indeterminate(
    monkeypatch, status_code
):
    result, upload_client = await _direct_upload_result(monkeypatch, status_code)

    assert result.success is False
    assert result.outcome == "indeterminate"
    assert result.correlation_id == "11111111-1111-4111-8111-111111111111"
    assert result.error == "Canvas file upload outcome is indeterminate"
    assert result.provider_result == {
        "phase": "upload",
        "request_accepted": True,
        "upload_status": status_code,
        "status_code": status_code,
    }
    assert upload_client.post_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 413, 415, 422])
async def test_canvas_post_body_documented_safe_rejection_is_definite(
    monkeypatch, status_code
):
    result, upload_client = await _direct_upload_result(monkeypatch, status_code)

    assert result.success is False
    assert result.outcome == "definite_failure"
    assert result.correlation_id is None
    assert result.error == "Canvas file upload failed"
    assert upload_client.post_calls == 1


@pytest.mark.asyncio
async def test_canvas_post_body_unknown_4xx_is_conservatively_indeterminate(
    monkeypatch,
):
    result, _ = await _direct_upload_result(monkeypatch, 418)

    assert result.outcome == "indeterminate"
    assert result.correlation_id == "11111111-1111-4111-8111-111111111111"


@pytest.mark.asyncio
async def test_canvas_direct_upload_success(monkeypatch):
    result, upload_client = await _direct_upload_result(monkeypatch, 201)

    assert result.success is True
    assert result.outcome == "success"
    assert result.file_id == "77"
    assert result.file_name == "fixed.pdf"
    assert upload_client.post_calls == 1
