#!/usr/bin/env python3
"""
Vision Model Benchmark Suite for Aelira

Tests vision models (Moondream, LLaVA, etc.) on real image description tasks
to measure accuracy and quality for accessibility alt-text generation.

Usage:
    python benchmarks/vision_benchmark.py
    python benchmarks/vision_benchmark.py --model moondream
    python benchmarks/vision_benchmark.py --download-samples
"""

import argparse
import json
import time
import os
import base64
import httpx
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

# Try to import PIL, but don't fail if not available
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("Note: PIL not available, using pre-generated or downloaded images")

# Ollama API endpoint
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Vision models to test
VISION_MODELS = [
    "moondream",           # Moondream2 1.4B - fast, small
    "llava:7b",            # LLaVA 7B - more accurate but slower
    "llava:13b",           # LLaVA 13B - even more accurate
    "minicpm-v",           # MiniCPM-V 2.6 - efficient multimodal
]

# Test images using public domain images from various sources
# These are real images that test various accessibility scenarios
TEST_IMAGES = [
    {
        "id": "pineapple",
        "url": "https://www.w3schools.com/cssref/pineapple.jpg",
        "description": "A pineapple",
        "expected_keywords": ["pineapple", "fruit", "yellow", "tropical"],
        "category": "photo",
    },
    {
        "id": "cat-photo",
        "url": "https://placekitten.com/400/300",
        "description": "A cat photo",
        "expected_keywords": ["cat", "kitten", "animal", "fur", "eyes"],
        "category": "photo",
    },
    {
        "id": "warning-sign",
        "url": "https://cdn-icons-png.flaticon.com/512/595/595067.png",
        "description": "A warning sign icon",
        "expected_keywords": ["warning", "triangle", "exclamation", "alert", "caution", "danger"],
        "category": "icon",
    },
    {
        "id": "user-icon",
        "url": "https://cdn-icons-png.flaticon.com/512/1077/1077114.png",
        "description": "A person/user icon",
        "expected_keywords": ["person", "user", "human", "silhouette", "icon", "profile", "head"],
        "category": "icon",
    },
    {
        "id": "checkmark",
        "url": "https://cdn-icons-png.flaticon.com/512/845/845646.png",
        "description": "A green checkmark",
        "expected_keywords": ["check", "green", "tick", "correct", "yes", "success", "mark"],
        "category": "icon",
    },
    {
        "id": "document",
        "url": "https://cdn-icons-png.flaticon.com/512/2991/2991108.png",
        "description": "A document or file icon",
        "expected_keywords": ["document", "file", "paper", "page", "text", "note"],
        "category": "icon",
    },
    {
        "id": "accessibility",
        "url": "https://cdn-icons-png.flaticon.com/512/1732/1732548.png",
        "description": "An accessibility icon with a person",
        "expected_keywords": ["accessibility", "wheelchair", "person", "disabled", "human", "disability"],
        "category": "icon",
    },
    {
        "id": "email-icon",
        "url": "https://cdn-icons-png.flaticon.com/512/561/561127.png",
        "description": "An email/envelope icon",
        "expected_keywords": ["email", "mail", "envelope", "message", "letter"],
        "category": "icon",
    },
]


@dataclass
class VisionBenchmarkResult:
    model: str
    test_id: str
    category: str
    success: bool
    inference_time_ms: float
    keywords_found: int
    keywords_total: int
    keyword_accuracy: float
    response_length: int
    response_preview: str
    error: Optional[str] = None


def download_test_image(spec: Dict[str, Any], output_dir: Path) -> Optional[str]:
    """Download a test image from URL."""
    output_path = output_dir / f"{spec['id']}.png"

    # Skip if already downloaded
    if output_path.exists():
        return str(output_path)

    # Use proper headers to avoid 403 errors
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = httpx.get(spec["url"], timeout=30.0, follow_redirects=True, headers=headers)
        response.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(response.content)
        return str(output_path)
    except Exception as e:
        print(f"  Failed to download {spec['id']}: {e}")
        return None


def get_installed_models() -> List[str]:
    """Get list of installed Ollama models."""
    try:
        response = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
        data = response.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def run_vision_inference(model: str, image_path: str, prompt: str) -> tuple[str, float]:
    """Run vision model inference on an image.

    Returns:
        Tuple of (response_text, inference_time_ms)
    """
    # Read and encode image
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    start_time = time.perf_counter()

    try:
        response = httpx.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                    "images": [image_data]
                }],
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 200
                }
            },
            timeout=120.0
        )
        response.raise_for_status()
        data = response.json()

        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000

        content = data.get("message", {}).get("content", "")
        return content, elapsed_ms

    except Exception as e:
        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000
        return f"ERROR: {e}", elapsed_ms


def benchmark_vision_model(model: str, test_images_dir: Path, image_paths: Dict[str, str]) -> List[VisionBenchmarkResult]:
    """Benchmark a vision model on all test images."""
    results = []

    prompt = """Describe this image for a screen reader user who cannot see it.
Be concise but accurate. Focus on the main visual elements, text content, and purpose.
Keep your description under 100 words."""

    for spec in TEST_IMAGES:
        image_path = image_paths.get(spec["id"])

        if not image_path:
            results.append(VisionBenchmarkResult(
                model=model,
                test_id=spec["id"],
                category=spec["category"],
                success=False,
                inference_time_ms=0,
                keywords_found=0,
                keywords_total=len(spec["expected_keywords"]),
                keyword_accuracy=0.0,
                response_length=0,
                response_preview="",
                error="Test image not found"
            ))
            continue

        response, elapsed_ms = run_vision_inference(model, image_path, prompt)

        # Check for errors
        if response.startswith("ERROR:"):
            results.append(VisionBenchmarkResult(
                model=model,
                test_id=spec["id"],
                category=spec["category"],
                success=False,
                inference_time_ms=elapsed_ms,
                keywords_found=0,
                keywords_total=len(spec["expected_keywords"]),
                keyword_accuracy=0.0,
                response_length=0,
                response_preview=response[:200],
                error=response
            ))
            continue

        # Calculate keyword accuracy
        response_lower = response.lower()
        keywords_found = sum(1 for kw in spec["expected_keywords"] if kw.lower() in response_lower)
        keyword_accuracy = keywords_found / len(spec["expected_keywords"]) if spec["expected_keywords"] else 0

        results.append(VisionBenchmarkResult(
            model=model,
            test_id=spec["id"],
            category=spec["category"],
            success=True,
            inference_time_ms=elapsed_ms,
            keywords_found=keywords_found,
            keywords_total=len(spec["expected_keywords"]),
            keyword_accuracy=keyword_accuracy,
            response_length=len(response),
            response_preview=response[:200],
            error=None
        ))

    return results


def print_results(all_results: Dict[str, List[VisionBenchmarkResult]]):
    """Print formatted benchmark results."""
    print("\n" + "=" * 80)
    print(" VISION MODEL BENCHMARK RESULTS")
    print("=" * 80)

    for model, results in all_results.items():
        print(f"\n## {model}")
        print("-" * 60)

        successful = [r for r in results if r.success]
        if not successful:
            print("  No successful tests!")
            continue

        avg_time = sum(r.inference_time_ms for r in successful) / len(successful)
        avg_accuracy = sum(r.keyword_accuracy for r in successful) / len(successful)
        success_rate = len(successful) / len(results)

        print(f"  Avg Time: {avg_time:.0f}ms | Keyword Accuracy: {avg_accuracy:.0%} | Success: {success_rate:.0%}")
        print()

        # Results by category
        categories = set(r.category for r in results)
        for category in sorted(categories):
            cat_results = [r for r in results if r.category == category]
            cat_successful = [r for r in cat_results if r.success]
            if cat_successful:
                cat_accuracy = sum(r.keyword_accuracy for r in cat_successful) / len(cat_successful)
                print(f"    {category}: {cat_accuracy:.0%} accuracy")

        print()
        print("  Sample responses:")
        for r in successful[:3]:
            status = "✓" if r.keyword_accuracy >= 0.5 else "⚠"
            print(f"    {status} [{r.test_id}] {r.keyword_accuracy:.0%} - {r.response_preview[:80]}...")


def main():
    parser = argparse.ArgumentParser(description="Vision Model Benchmark for Aelira")
    parser.add_argument("--model", type=str, help="Test specific model only")
    parser.add_argument("--download-samples", action="store_true", help="Download sample images")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    args = parser.parse_args()

    # Create test images directory
    test_images_dir = Path(__file__).parent / "test_images"
    test_images_dir.mkdir(exist_ok=True)

    # Download test images
    print("Downloading test images from Wikimedia Commons...")
    image_paths = {}
    for spec in TEST_IMAGES:
        path = download_test_image(spec, test_images_dir)
        if path:
            image_paths[spec["id"]] = path
            print(f"  ✓ {spec['id']}")
        else:
            print(f"  ✗ {spec['id']} (download failed)")

    print(f"\nDownloaded {len(image_paths)}/{len(TEST_IMAGES)} test images")

    # Check Ollama availability
    try:
        response = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
        if response.status_code != 200:
            print(f"ERROR: Ollama not accessible at {OLLAMA_HOST}")
            return
    except Exception as e:
        print(f"ERROR: Cannot connect to Ollama: {e}")
        return

    print("\n✓ Ollama is running")

    # Get available models
    installed = get_installed_models()
    print(f"Installed models: {', '.join(installed)}")

    # Determine which models to test
    if args.model:
        models_to_test = [args.model]
    else:
        # Test only installed vision models
        models_to_test = [m for m in VISION_MODELS if any(m in installed_model for installed_model in installed)]

    if not models_to_test:
        print("\nNo vision models found. Install moondream with: ollama pull moondream")
        return

    print(f"\nTesting models: {', '.join(models_to_test)}")

    # Run benchmarks
    all_results = {}
    for model in models_to_test:
        print(f"\n  Benchmarking {model}...")
        results = benchmark_vision_model(model, test_images_dir, image_paths)
        all_results[model] = results

    # Print results
    print_results(all_results)

    # Save results if requested
    if args.output:
        output_data = {
            model: [asdict(r) for r in results]
            for model, results in all_results.items()
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\n✓ Results saved to {args.output}")


if __name__ == "__main__":
    main()
