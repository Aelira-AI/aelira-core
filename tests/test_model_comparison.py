"""
Model comparison tests for Ollama models.

Tests each model against the same inputs to compare quality, speed, and accuracy.
Run with: python3 tests/test_model_comparison.py

Requires: Ollama running at localhost:11434 with models pulled.
"""

import json
import time
import httpx
import base64
import sys
from pathlib import Path
from typing import Optional

OLLAMA_URL = "http://localhost:11434"

# ============================================================
# Test inputs — representative of real Aelira workloads
# ============================================================

# 1. Alt text generation (vision models)
TEST_IMAGE_DESCRIPTION = """
A bar chart showing undergraduate enrollment trends at a university.
The x-axis shows years 2020-2025, the y-axis shows student count from 0-6000.
Bars are blue and show steady increase from 4,200 in 2020 to 5,400 in 2025.
"""

# 2. HTML accessibility fix (code models)
TEST_HTML_FIX = """
Fix this WCAG accessibility issue:

Issue: Images must have alternate text (image-alt)
Impact: critical
Element: <img src="/campus/library-exterior.jpg" class="hero-image">
Context: This image is in the hero section of a university library webpage.

Provide:
EXPLANATION: [2-3 sentences explaining the fix]
CODE: [corrected HTML only]
"""

# 3. LaTeX to accessible description (text models)
TEST_LATEX_DESCRIPTION = """
Describe this mathematical expression in clear, accessible language
that would help a screen reader user understand the math:

E = mc^2

Provide a concise description (1-2 sentences) focusing on meaning, not symbols.
"""

# 4. Document OCR test prompt (vision models with an image)
TEST_OCR_PROMPT = """
This is a scanned document page. Extract all visible text exactly as written,
preserving the layout structure (headings, paragraphs, lists).
"""

# 5. Complex code fix — table accessibility
TEST_TABLE_FIX = """
Fix this WCAG accessibility issue:

Issue: Tables must have headers (td-headers-attr)
Impact: serious
Element:
<table class="grades">
  <tr><td>Student</td><td>Grade</td><td>Date</td></tr>
  <tr><td>Jane Doe</td><td>A</td><td>2026-03-15</td></tr>
  <tr><td>John Smith</td><td>B+</td><td>2026-03-15</td></tr>
</table>

Provide:
EXPLANATION: [2-3 sentences]
CODE: [corrected HTML with proper table headers, scope attributes, and caption]
"""


def call_ollama(model: str, prompt: str, system: str = None, max_tokens: int = 500) -> dict:
    """Call Ollama API and return response with timing."""
    start = time.time()

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.2,
        },
    }
    if system:
        payload["system"] = system

    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=120.0,
        )
        elapsed = time.time() - start

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:200]}",
                "elapsed": elapsed,
            }

        data = response.json()
        return {
            "success": True,
            "content": data.get("response", ""),
            "elapsed": elapsed,
            "eval_count": data.get("eval_count", 0),
            "tokens_per_sec": data.get("eval_count", 0) / elapsed if elapsed > 0 else 0,
        }
    except httpx.TimeoutException:
        return {"success": False, "error": "Timeout (120s)", "elapsed": 120.0}
    except Exception as e:
        return {"success": False, "error": str(e), "elapsed": time.time() - start}


def check_models_available() -> dict:
    """Check which models are available in Ollama."""
    try:
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=10.0)
        models = response.json().get("models", [])
        available = {m["name"].split(":")[0] + ":" + m["name"].split(":")[1] if ":" in m["name"] else m["name"] for m in models}
        return {
            "qwen3-coder": any("qwen3-coder" in m for m in available),
            "qwen3-coder-7b": any("qwen3-coder:7b" in m["name"] for m in models),
            "qwen2.5vl": any("qwen2.5vl" in m for m in available),
            "gemma3": any("gemma3" in m for m in available),
            "deepseek-ocr": any("deepseek-ocr" in m for m in available),
        }
    except Exception as e:
        print(f"Error checking models: {e}")
        return {}


def run_text_code_comparison():
    """Compare text/code models: qwen3-coder vs gemma3."""
    print("\n" + "=" * 70)
    print("TEXT/CODE MODEL COMPARISON")
    print("=" * 70)

    # qwen3-coder:30b needs 17.7GB — VPS only. Local dev uses qwen2.5-coder:7b
    models = ["qwen2.5-coder:7b", "gemma3:4b"]
    system = "You are an expert web accessibility developer specializing in WCAG 2.1 AA compliance."

    tests = [
        ("HTML Alt Text Fix", TEST_HTML_FIX),
        ("Table Accessibility Fix", TEST_TABLE_FIX),
        ("LaTeX Description", TEST_LATEX_DESCRIPTION),
    ]

    for test_name, prompt in tests:
        print(f"\n--- {test_name} ---")
        for model in models:
            result = call_ollama(model, prompt, system=system)
            if result["success"]:
                print(f"\n  [{model}] ({result['elapsed']:.1f}s, {result['tokens_per_sec']:.0f} tok/s)")
                # Show first 300 chars of response
                content = result["content"].strip()
                preview = content[:300] + "..." if len(content) > 300 else content
                for line in preview.split("\n"):
                    print(f"    {line}")
            else:
                print(f"\n  [{model}] FAILED: {result['error']}")


def run_vision_comparison():
    """Compare vision models: qwen2.5vl vs gemma3 vs deepseek-ocr."""
    print("\n" + "=" * 70)
    print("VISION MODEL COMPARISON")
    print("=" * 70)

    # For vision tests without a real image, use a text-based description test
    # Real image testing would require base64-encoded images
    models = ["qwen2.5vl:3b", "gemma3:4b"]

    prompt = f"""Based on this description of an image, generate appropriate alt text
for a screen reader user:

{TEST_IMAGE_DESCRIPTION}

Provide alt text in exactly one sentence, under 125 characters."""

    print("\n--- Alt Text Generation (from description) ---")
    for model in models:
        result = call_ollama(model, prompt)
        if result["success"]:
            print(f"\n  [{model}] ({result['elapsed']:.1f}s)")
            content = result["content"].strip()
            print(f"    Alt text: {content[:200]}")
            print(f"    Length: {len(content)} chars")
        else:
            print(f"\n  [{model}] FAILED: {result['error']}")


def run_ocr_comparison():
    """Test DeepSeek-OCR specific capabilities."""
    print("\n" + "=" * 70)
    print("OCR MODEL TEST (DeepSeek-OCR)")
    print("=" * 70)

    # Test with a structured text extraction prompt
    prompt = """Extract and structure the following information as if reading a scanned university syllabus:

Course: CS 101 - Introduction to Computer Science
Instructor: Dr. Sarah Chen
Office Hours: Mon/Wed 2-4pm, Room 314
Prerequisites: None

Learning Outcomes:
1. Understand fundamental programming concepts
2. Write programs in Python
3. Apply computational thinking to solve problems

Grading:
- Assignments: 40%
- Midterm: 25%
- Final Project: 35%

Reproduce this exactly as structured text, preserving all formatting."""

    result = call_ollama("deepseek-ocr:3b", prompt)
    if result["success"]:
        print(f"\n  [deepseek-ocr:3b] ({result['elapsed']:.1f}s)")
        print(f"  Output:")
        for line in result["content"].strip().split("\n"):
            print(f"    {line}")
    else:
        print(f"\n  [deepseek-ocr:3b] FAILED: {result['error']}")


def run_speed_benchmark():
    """Quick speed benchmark — same simple prompt across all models."""
    print("\n" + "=" * 70)
    print("SPEED BENCHMARK (same prompt, all models)")
    print("=" * 70)

    prompt = "Explain in one sentence what WCAG 2.1 AA compliance means for universities."
    models = ["qwen2.5-coder:7b", "qwen2.5vl:3b", "gemma3:4b", "deepseek-ocr:3b"]

    results = []
    for model in models:
        result = call_ollama(model, prompt, max_tokens=100)
        if result["success"]:
            results.append((model, result["elapsed"], result["tokens_per_sec"], result["content"].strip()[:100]))
            print(f"  {model:25s} — {result['elapsed']:5.1f}s — {result['tokens_per_sec']:5.0f} tok/s")
        else:
            print(f"  {model:25s} — FAILED: {result['error']}")

    if results:
        fastest = min(results, key=lambda x: x[1])
        print(f"\n  Fastest: {fastest[0]} ({fastest[1]:.1f}s)")


def main():
    print("Aelira Model Comparison Test Suite")
    print(f"Ollama endpoint: {OLLAMA_URL}")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Check which models are available
    available = check_models_available()
    print(f"\nAvailable models:")
    for model, ready in available.items():
        status = "✓ ready" if ready else "✗ not pulled"
        print(f"  {model:20s} {status}")

    if not any(available.values()):
        print("\nNo models available! Pull models first:")
        print("  docker exec aelira-ollama-dev ollama pull qwen3-coder:30b")
        print("  docker exec aelira-ollama-dev ollama pull qwen2.5vl:3b")
        print("  docker exec aelira-ollama-dev ollama pull gemma3:4b")
        print("  docker exec aelira-ollama-dev ollama pull deepseek-ocr:3b")
        sys.exit(1)

    # Run available tests
    if available.get("qwen3-coder") or available.get("gemma3"):
        run_text_code_comparison()

    if available.get("qwen2.5vl") or available.get("gemma3"):
        run_vision_comparison()

    if available.get("deepseek-ocr"):
        run_ocr_comparison()

    # Speed benchmark with whatever is available
    run_speed_benchmark()

    print("\n" + "=" * 70)
    print("COMPARISON COMPLETE")
    print("=" * 70)
    print("\nReview the outputs above and update docs/TODO-model-testing.md with results.")


if __name__ == "__main__":
    main()
