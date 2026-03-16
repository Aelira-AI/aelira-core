#!/usr/bin/env python3
"""
Gemini Vision Model Benchmark for Aelira

Tests Google Gemini models on image description tasks for accessibility alt-text generation.
Uses the same test images as vision_benchmark.py for direct comparison with local models.

Usage:
    python benchmarks/gemini_vision_benchmark.py
    python benchmarks/gemini_vision_benchmark.py --model gemini-1.5-flash
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

# Gemini API endpoint
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Available Gemini models with vision
# Rate limits (Nov 2025): RPM = requests per minute
GEMINI_MODELS = [
    "gemini-2.0-flash",              # 2K RPM - Fast, good quality (free tier)
    "gemini-2.0-flash-lite",         # 4K RPM - Fastest (free tier)
    "gemini-2.5-flash",              # 1K RPM - Latest flash (paid tier)
    "gemini-2.5-flash-image",        # 500 RPM - Image-optimized flash (paid tier)
    "gemini-2.5-flash-image-preview", # Image-optimized flash preview (paid tier)
    "gemini-2.5-pro",                # 150 RPM - High quality (paid tier)
    "nano-banana-pro-preview",       # Nano Banana Pro - Fast image model (paid tier)
    "gemini-3-pro-preview",          # 25 RPM - Newest general model (paid tier)
    "gemini-3-pro-image-preview",    # 20 RPM - Image-specific model (paid tier)
]

# Same test images as vision_benchmark.py for comparison
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

    if output_path.exists():
        return str(output_path)

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
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


def get_mime_type(file_path: str) -> str:
    """Get MIME type based on file extension."""
    ext = Path(file_path).suffix.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return mime_types.get(ext, "image/png")


# Models that use "thinking mode" and need higher token limits
THINKING_MODELS = ["gemini-2.5", "gemini-3"]


def run_gemini_inference(model: str, image_path: str, prompt: str, api_key: str) -> tuple[str, float]:
    """Run Gemini vision model inference on an image."""
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    mime_type = get_mime_type(image_path)

    # Models with thinking mode need higher token limits
    is_thinking_model = any(t in model for t in THINKING_MODELS)
    max_tokens = 2000 if is_thinking_model else 200

    start_time = time.perf_counter()

    try:
        response = httpx.post(
            f"{GEMINI_API_BASE}/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": image_data
                            }
                        }
                    ]
                }],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": max_tokens,
                }
            },
            timeout=120.0  # Longer timeout for thinking models
        )

        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000

        if response.status_code != 200:
            error_detail = response.text[:500]
            return f"ERROR: {response.status_code} - {error_detail}", elapsed_ms

        data = response.json()

        # Extract text from Gemini response
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                content = parts[0].get("text", "")
                return content, elapsed_ms

        return "ERROR: No content in response", elapsed_ms

    except Exception as e:
        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000
        return f"ERROR: {e}", elapsed_ms


def benchmark_gemini_model(model: str, image_paths: Dict[str, str], api_key: str) -> List[VisionBenchmarkResult]:
    """Benchmark a Gemini model on all test images."""
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

        print(f"    Testing {spec['id']}...", end=" ", flush=True)
        response, elapsed_ms = run_gemini_inference(model, image_path, prompt, api_key)

        # Rate limit buffer based on model (gemini-3-pro-image = 5 RPM = 12s between requests)
        if "gemini-3" in model:
            time.sleep(15)  # 15s for Gemini 3 models (conservative for 5 RPM limit)
        else:
            time.sleep(3)  # 3s for other models

        if response.startswith("ERROR:"):
            print(f"ERROR ({elapsed_ms:.0f}ms)")
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

        print(f"{keyword_accuracy:.0%} ({elapsed_ms:.0f}ms)")

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
    print(" GEMINI VISION MODEL BENCHMARK RESULTS")
    print("=" * 80)

    for model, results in all_results.items():
        print(f"\n## {model}")
        print("-" * 60)

        successful = [r for r in results if r.success]
        if not successful:
            print("  No successful tests!")
            for r in results:
                if r.error:
                    print(f"    {r.test_id}: {r.error[:80]}")
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
    parser = argparse.ArgumentParser(description="Gemini Vision Model Benchmark for Aelira")
    parser.add_argument("--model", type=str, default="gemini-1.5-flash", help="Gemini model to test")
    parser.add_argument("--api-key", type=str, help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    parser.add_argument("--all-models", action="store_true", help="Test all available models")
    args = parser.parse_args()

    # Get API key
    api_key = args.api_key or GEMINI_API_KEY
    if not api_key:
        print("ERROR: No API key provided. Set GEMINI_API_KEY or use --api-key")
        return

    # Create test images directory
    test_images_dir = Path(__file__).parent / "test_images"
    test_images_dir.mkdir(exist_ok=True)

    # Download test images
    print("Downloading test images...")
    image_paths = {}
    for spec in TEST_IMAGES:
        path = download_test_image(spec, test_images_dir)
        if path:
            image_paths[spec["id"]] = path
            print(f"  ✓ {spec['id']}")
        else:
            print(f"  ✗ {spec['id']} (download failed)")

    print(f"\nDownloaded {len(image_paths)}/{len(TEST_IMAGES)} test images")

    # Determine models to test
    if args.all_models:
        models_to_test = GEMINI_MODELS
    else:
        models_to_test = [args.model]

    print(f"\nTesting models: {', '.join(models_to_test)}")

    # Run benchmarks
    all_results = {}
    for model in models_to_test:
        print(f"\n  Benchmarking {model}...")
        results = benchmark_gemini_model(model, image_paths, api_key)
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
