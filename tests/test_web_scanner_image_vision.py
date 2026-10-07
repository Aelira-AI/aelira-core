"""Image descriptions require verified source pixels, never text-only markup."""

from io import BytesIO

import pytest
from PIL import Image

from src.education.web_scanner import WebPageIssue, WebScanner
from src.utils import security


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield


class ImageResponse:
    def __init__(self, status, payload=b""):
        self.status_code = status
        self.payload = payload
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset : offset + chunk_size]


def _png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def _scanner():
    scanner = WebScanner.__new__(WebScanner)
    scanner.llm_client = None
    return scanner


def test_vision_request_contains_verified_source_pixels(monkeypatch):
    pixels = _png_bytes()
    fetched = ImageResponse(200, pixels)
    monkeypatch.setattr(security, "safe_requests_get", lambda *args, **kwargs: fetched)
    observed = []

    class VisionProvider:
        provider = "local"

        def analyze_image_sync(self, *, image_data, prompt, max_tokens):
            observed.append((image_data, prompt, max_tokens))
            return {
                "success": True,
                "content": "A red square",
                "provider": "local",
                "model": "fixture-vision",
            }

    scanner = _scanner()
    scanner.llm_client = VisionProvider()
    assert (
        scanner._call_image_scanner_api("https://public.test/picture.png")
        == "A red square"
    )
    assert len(observed) == 1
    assert observed[0][0] == pixels
    assert "Describe this image" in observed[0][1]
    assert fetched.closed


def test_alt_validation_uses_the_same_source_pixels(monkeypatch):
    pixels = _png_bytes()
    monkeypatch.setattr(
        security,
        "safe_requests_get",
        lambda *args, **kwargs: ImageResponse(200, pixels),
    )
    observed = []

    class VisionProvider:
        provider = "local"

        def analyze_image_sync(self, *, image_data, prompt, max_tokens):
            observed.append((image_data, prompt))
            return {
                "success": True,
                "content": '{"is_accurate":true,"accuracy_score":0.9,"issues":[],"reasoning":"Matches"}',
                "provider": "local",
                "model": "fixture-vision",
            }

    scanner = _scanner()
    scanner.llm_client = VisionProvider()
    result = scanner._call_image_validation_api(
        "https://public.test/picture.png", "A red square"
    )
    assert result["is_accurate"] is True
    assert observed[0][0] == pixels
    assert 'EXISTING ALT TEXT: "A red square"' in observed[0][1]


def test_malformed_vision_validation_does_not_invent_accuracy(monkeypatch):
    pixels = _png_bytes()
    monkeypatch.setattr(
        security,
        "safe_requests_get",
        lambda *args, **kwargs: ImageResponse(200, pixels),
    )

    class VisionProvider:
        provider = "local"

        def analyze_image_sync(self, **kwargs):
            assert kwargs["image_data"] == pixels
            return {
                "success": True,
                "content": "perhaps accurate",
                "provider": "local",
                "model": "fixture-vision",
            }

    scanner = _scanner()
    scanner.llm_client = VisionProvider()
    assert (
        scanner._call_image_validation_api(
            "https://public.test/picture.png", "A red square"
        )
        is None
    )


@pytest.mark.parametrize(
    "response",
    [
        ImageResponse(404),
        ImageResponse(200, b"<html>missing image</html>"),
        ImageResponse(200, b"x" * (10 * 1024 * 1024 + 1)),
    ],
)
def test_missing_invalid_or_oversized_image_never_reaches_vision(monkeypatch, response):
    monkeypatch.setattr(security, "safe_requests_get", lambda *args, **kwargs: response)

    class VisionProvider:
        def analyze_image_sync(self, **kwargs):
            pytest.fail("vision called without image pixels")

    scanner = _scanner()
    scanner.llm_client = VisionProvider()
    assert scanner._call_image_scanner_api("https://public.test/missing.png") is None
    assert response.closed


def test_private_image_never_reaches_vision(monkeypatch):
    def refuse(*args, **kwargs):
        raise ValueError("URL target is not allowed")

    monkeypatch.setattr(security, "safe_requests_get", refuse)

    class VisionProvider:
        def analyze_image_sync(self, **kwargs):
            pytest.fail("vision called for private image")

    scanner = _scanner()
    scanner.llm_client = VisionProvider()
    assert scanner._call_image_scanner_api("http://private.test/image.png") is None


def test_image_alt_text_only_fix_paths_require_human_review():
    scanner = _scanner()

    class TextModel:
        def generate_code_sync(self, **kwargs):
            pytest.fail("text-only code model received an image-alt issue")

        def generate_text_sync(self, **kwargs):
            pytest.fail("text-only model received an image-alt issue")

    scanner.llm_client = TextModel()
    issue = WebPageIssue(
        impact="critical",
        criterion="wcag2a",
        rule_id="image-alt",
        description="Image has no alt",
        help_url="",
        element='<img src="missing.png">',
        fix="Review image",
    )
    scanner._batch_generate_fixes([issue])

    assert issue.generated_code_fix is None
    assert scanner._generate_code_fix(
        "Image has no alt", '<img src="missing.png">', "", "image-alt"
    ) == ("Review the image and add alt text that conveys its purpose.", None)
