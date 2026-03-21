"""Tests for image alt text generation."""

import pytest
import os
from src.education.image_alt_text import ImageAltTextGenerator


@pytest.fixture
def generator():
    """Create image alt text generator."""
    return ImageAltTextGenerator()


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
@pytest.mark.skipif(not os.getenv("SKIP_VISION_TESTS"), reason="Requires test images")
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
@pytest.mark.skipif(not os.getenv("SKIP_VISION_TESTS"), reason="Requires test images")
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
