"""Tests for image alt text generation."""

import ast
import inspect
from unittest.mock import MagicMock, patch

import pytest
import os
from src.education.image_alt_text import ImageAltTextGenerator


@pytest.fixture
def generator():
    """Create image alt text generator."""
    return ImageAltTextGenerator(allow_legacy_transport=True)


def _image(tmp_path):
    from PIL import Image

    path = tmp_path / "image.png"
    Image.new("RGB", (10, 10), color="blue").save(path)
    return str(path)


@pytest.mark.asyncio
async def test_safe_default_has_no_vision_transport(tmp_path):
    generator = ImageAltTextGenerator()

    with (
        patch.object(
            generator,
            "_generate_with_gemini",
            side_effect=AssertionError("legacy Gemini forbidden"),
        ),
        patch.object(
            generator,
            "_generate_with_ollama",
            side_effect=AssertionError("legacy Ollama forbidden"),
        ),
    ):
        result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is False
    assert result["error"] == "AI transport not authorized"
    assert result["provider"] == "none"


@pytest.mark.asyncio
async def test_injected_lms_client_is_the_only_alt_text_transport(tmp_path):
    client = MagicMock()
    client.provider = "gemini"
    client.analyze_image_sync.return_value = {
        "success": True,
        "content": "Blue square",
        "inference_time": 0.2,
        "provider": "gemini",
        "model": "vision-safe",
    }
    generator = ImageAltTextGenerator(lms_client=client)

    with (
        patch.object(
            generator,
            "_generate_with_gemini",
            side_effect=AssertionError("legacy Gemini forbidden"),
        ),
        patch.object(
            generator,
            "_generate_with_ollama",
            side_effect=AssertionError("legacy Ollama forbidden"),
        ),
    ):
        result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    assert result["alt_text"] == "Blue square"
    assert result["provider"] == "gemini"
    client.analyze_image_sync.assert_called_once()
    assert client.analyze_image_sync.call_args.kwargs["image_data"].startswith(
        b"\x89PNG"
    )


@pytest.mark.asyncio
async def test_legacy_gemini_success_records_bounded_external_usage(tmp_path):
    generator = ImageAltTextGenerator(allow_legacy_transport=True)
    generator.use_gemini = True
    generator.vision_model = "gemini-safe"

    with patch.object(
        generator, "_generate_with_gemini", return_value=("Blue square", 0.2)
    ):
        result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    assert dict(generator.usage_metadata) == {
        "ai_used": True,
        "external_ai_used": True,
        "providers_attempted": ("gemini",),
        "provider": "gemini",
        "model": "gemini-safe",
        "outcome": "used",
    }


@pytest.mark.asyncio
async def test_direct_ollama_success_records_local_usage(tmp_path):
    generator = ImageAltTextGenerator(allow_legacy_transport=True)
    generator.use_gemini = False
    generator.ollama_fallback = "llava-safe"

    with patch.object(
        generator, "_generate_with_ollama", return_value=("Blue square", 0.1)
    ):
        await generator.generate_alt_text(_image(tmp_path))

    assert dict(generator.usage_metadata) == {
        "ai_used": True,
        "external_ai_used": False,
        "providers_attempted": ("ollama",),
        "provider": "ollama",
        "model": "llava-safe",
        "outcome": "used",
    }


@pytest.mark.asyncio
async def test_gemini_failure_then_ollama_success_preserves_external_attempt(tmp_path):
    generator = ImageAltTextGenerator(allow_legacy_transport=True)
    generator.use_gemini = True
    generator.vision_model = "gemini-safe"
    generator.ollama_fallback = "llava-safe"

    with (
        patch.object(
            generator, "_generate_with_gemini", return_value=("ERROR: unavailable", 0.2)
        ),
        patch.object(
            generator, "_generate_with_ollama", return_value=("Blue square", 0.1)
        ),
    ):
        await generator.generate_alt_text(_image(tmp_path))

    assert dict(generator.usage_metadata) == {
        "ai_used": True,
        "external_ai_used": True,
        "providers_attempted": ("gemini", "ollama"),
        "provider": "ollama",
        "model": "llava-safe",
        "outcome": "used",
    }


@pytest.mark.asyncio
async def test_legacy_failures_remain_attempted_and_usage_resets_without_leakage(
    tmp_path,
):
    generator = ImageAltTextGenerator(allow_legacy_transport=True)
    generator.use_gemini = True
    generator.ollama_fallback = "llava-safe"

    with (
        patch.object(
            generator,
            "_generate_with_gemini",
            return_value=("ERROR: SENSITIVE gemini failure", 0.2),
        ),
        patch.object(
            generator,
            "_generate_with_ollama",
            return_value=("ERROR: SENSITIVE ollama failure", 0.1),
        ),
    ):
        result = await generator.generate_alt_text(_image(tmp_path))

    usage = dict(generator.usage_metadata)
    assert result["success"] is False
    assert usage == {
        "ai_used": False,
        "external_ai_used": True,
        "providers_attempted": ("gemini", "ollama"),
        "provider": "ollama",
        "model": "llava-safe",
        "outcome": "attempted_failed",
    }
    assert "SENSITIVE" not in str(usage)

    generator.use_gemini = False
    with patch.object(
        generator, "_generate_with_ollama", return_value=("Blue square", 0.1)
    ):
        await generator.generate_alt_text(_image(tmp_path))
    assert generator.usage_metadata["providers_attempted"] == ("ollama",)


@pytest.mark.asyncio
async def test_injected_lms_failure_never_falls_back_or_classifies(tmp_path):
    client = MagicMock()
    client.provider = "ollama"
    client.analyze_image_sync.return_value = {
        "success": False,
        "error": "policy_denied",
        "provider": "ollama",
        "model": "",
    }
    generator = ImageAltTextGenerator(lms_client=client)

    with (
        patch.object(
            generator,
            "_generate_with_gemini",
            side_effect=AssertionError("legacy Gemini forbidden"),
        ),
        patch.object(
            generator,
            "_generate_with_ollama",
            side_effect=AssertionError("legacy Ollama forbidden"),
        ),
    ):
        result = await generator.detect_image_type(_image(tmp_path))

    assert result["success"] is False
    assert result["error"] == "policy_denied"
    assert client.analyze_image_sync.call_count == 1


@pytest.mark.asyncio
async def test_injected_lms_coherent_dispatch_denial_records_no_transport_attempt(
    tmp_path,
):
    client = MagicMock()
    client.provider = "gemini"
    client.analyze_image_sync.return_value = {
        "success": False,
        "error": "policy_denied",
        "ai_used": False,
        "external_ai_used": False,
        "purpose_outcome": "denied_at_dispatch",
        "provider": "ollama",
        "model": "spoofed-model",
    }
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is False
    assert dict(generator.usage_metadata) == {
        "ai_used": False,
        "external_ai_used": False,
        "providers_attempted": (),
        "provider": "gemini",
        "model": None,
        "outcome": "denied_at_dispatch",
    }


def test_lms_vision_paths_have_no_direct_legacy_transport_or_manager_acquisition():
    guarded = [
        ImageAltTextGenerator.generate_alt_text,
        ImageAltTextGenerator.validate_alt_text,
        ImageAltTextGenerator.detect_image_type,
        ImageAltTextGenerator.describe_chart_or_graph,
        ImageAltTextGenerator.score_alt_text_quality,
    ]
    forbidden = {
        "_generate_with_gemini",
        "_generate_with_ollama",
        "get_provider_manager",
    }
    class_tree = ast.parse(inspect.getsource(ImageAltTextGenerator))
    methods = {
        node.name: node
        for node in ast.walk(class_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    violations = []
    for method in guarded:
        tree = methods[method.__name__]
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        found = forbidden & (names | attrs)
        if found:
            violations.append(f"{method.__name__}: {sorted(found)}")

    assert violations == []


def test_health_check(generator):
    """Test Gemini/Ollama vision model availability."""
    result = generator.health_check()
    print("\n=== Health Check ===")
    print(f"Status: {result['status']}")
    print(f"Gemini Model: {result.get('gemini_model', 'N/A')}")
    print(f"Gemini Configured: {result.get('gemini_configured', False)}")
    print(f"Use Gemini: {result.get('use_gemini', False)}")
    print(f"Features: {result.get('features', [])}")

    assert result["status"] in ["healthy", "model_missing", "unhealthy", "degraded"]


@pytest.mark.asyncio
async def test_image_validation_nonexistent(generator):
    """Test validation of non-existent image."""
    result = await generator.generate_alt_text("nonexistent.jpg")

    assert result["success"] is False
    assert "File not found" in result["error"]


@pytest.mark.asyncio
async def test_image_validation_invalid_format(generator, tmp_path):
    """Test validation of invalid image format."""
    # Create a text file with .txt extension
    text_file = tmp_path / "test.txt"
    text_file.write_text("not an image")

    result = await generator.generate_alt_text(str(text_file))

    assert result["success"] is False
    assert "Unsupported format" in result["error"]


@pytest.mark.asyncio
@pytest.mark.integration  # needs a live vision provider (Gemini/Ollama)
@pytest.mark.skipif(
    os.getenv("SKIP_VISION_TESTS"),
    reason="SKIP_VISION_TESTS set; vision tests opted out",
)
async def test_generate_alt_text_chart(generator):
    """Test alt text generation for chart image.

    Note: Requires a sample chart image at tests/fixtures/sample_chart.png
    """
    image_path = "tests/fixtures/sample_chart.png"

    if not os.path.exists(image_path):
        pytest.skip("Sample chart image not found")

    result = await generator.generate_alt_text(
        image_path=image_path,
        context="Statistics lecture slide showing student performance data",
        educational_context=True,
    )

    print("\n=== Chart Analysis ===")
    print(f"Success: {result.get('success')}")
    print(f"Alt Text: {result.get('alt_text')}")
    print(f"Long Description: {result.get('long_description')}")
    print(f"Image Type: {result.get('image_type')}")
    print(f"Educational Value: {result.get('educational_value')}")
    print(f"Contains Text: {result.get('contains_text')}")
    print(f"Inference Time: {result.get('inference_time'):.2f}s")

    assert result["success"] is True
    assert "alt_text" in result
    assert "long_description" in result
    assert len(result["alt_text"]) <= 125  # WCAG recommendation


@pytest.mark.asyncio
@pytest.mark.integration  # needs a live vision provider (Gemini/Ollama)
@pytest.mark.skipif(
    os.getenv("SKIP_VISION_TESTS"),
    reason="SKIP_VISION_TESTS set; vision tests opted out",
)
async def test_batch_generate_alt_text(generator):
    """Test batch alt text generation.

    Note: Requires sample images in tests/fixtures/
    """
    image_paths = [
        "tests/fixtures/sample_chart.png",
        "tests/fixtures/sample_diagram.png",
        "tests/fixtures/sample_photo.jpg",
    ]

    # Filter to only existing images
    existing_images = [p for p in image_paths if os.path.exists(p)]

    if not existing_images:
        pytest.skip("No sample images found for batch test")

    result = await generator.batch_generate_alt_text(
        image_paths=existing_images, educational_context=True
    )

    print("\n=== Batch Processing ===")
    print(f"Total Images: {result['total_images']}")
    print(f"Success Count: {result['success_count']}")
    print(f"Failed Count: {result['failed_count']}")
    print(f"Total Time: {result['total_inference_time']:.2f}s")
    print(f"Average Time: {result['average_time_per_image']:.2f}s")

    assert result["total_images"] == len(existing_images)
    assert result["success_count"] > 0


def test_encode_image(generator, tmp_path):
    """Test base64 image encoding."""
    from PIL import Image

    # Create a simple test image
    img = Image.new("RGB", (100, 100), color="red")
    img_path = tmp_path / "test.png"
    img.save(img_path)

    # Encode image
    encoded = generator._encode_image(str(img_path))

    assert encoded is not None
    assert len(encoded) > 0
    assert isinstance(encoded, str)


def test_validate_image_valid(generator, tmp_path):
    """Test validation of valid image."""
    from PIL import Image

    # Create a simple test image
    img = Image.new("RGB", (100, 100), color="blue")
    img_path = tmp_path / "test.png"
    img.save(img_path)

    # Validate
    result = generator._validate_image(str(img_path))

    assert result["valid"] is True
    assert result["width"] == 100
    assert result["height"] == 100
    assert result["format"] == "PNG"
    assert result["size_bytes"] > 0


if __name__ == "__main__":
    # Run tests manually for debugging
    gen = ImageAltTextGenerator()

    print("Running manual tests...")

    # Health check
    health = gen.health_check()
    print("\n=== Manual Health Check ===")
    print(f"Status: {health['status']}")
    print(f"Vision Available: {health['vision_available']}")

    # If llava is available, test with a simple image
    if health["vision_available"]:
        print("\n✅ llava model is available")
        print("Create sample images in tests/fixtures/ to test alt text generation")
    else:
        print("\n❌ llava model not found")
        print("Run: ollama pull llava:7b")
