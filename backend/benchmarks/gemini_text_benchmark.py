#!/usr/bin/env python3
"""
Gemini Text Model Benchmark for Aelira

Tests Google Gemini models on accessibility classification, code generation,
and summary tasks to evaluate cloud API vs local Ollama models.

Usage:
    python benchmarks/gemini_text_benchmark.py
    python benchmarks/gemini_text_benchmark.py --model gemini-2.0-flash
"""

import argparse
import json
import time
import os
import httpx
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

# Gemini API endpoint
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Available Gemini models for text
GEMINI_MODELS = [
    "gemini-2.0-flash",              # Fast, good quality (free tier)
    "gemini-2.0-flash-lite",         # Faster, smaller (free tier)
    "gemini-2.5-flash",              # Latest flash (paid tier)
    "gemini-2.5-pro",                # High quality (paid tier)
]

# Test cases for accessibility classification
CLASSIFICATION_TESTS = [
    {
        "id": "missing-alt-critical",
        "rule_id": "image-alt",
        "impact": "critical",
        "html": '<img src="checkout-button.png" onclick="checkout()">',
        "selector": "img.checkout-btn",
        "expected_severity": "Critical",
        "description": "Functional image without alt text in checkout flow"
    },
    {
        "id": "missing-alt-decorative",
        "rule_id": "image-alt",
        "impact": "minor",
        "html": '<img src="decorative-border.png">',
        "selector": "img.border",
        "expected_severity": "Low",
        "description": "Decorative image that should have empty alt"
    },
    {
        "id": "color-contrast-text",
        "rule_id": "color-contrast",
        "impact": "serious",
        "html": '<p style="color: #999; background: #fff">Important info</p>',
        "selector": "p.info",
        "expected_severity": "High",
        "description": "Low contrast text that's hard to read"
    },
    {
        "id": "missing-label",
        "rule_id": "label",
        "impact": "critical",
        "html": '<input type="email" placeholder="Enter email">',
        "selector": "input[type=email]",
        "expected_severity": "Critical",
        "description": "Form input without accessible label"
    },
    {
        "id": "empty-link",
        "rule_id": "link-name",
        "impact": "serious",
        "html": '<a href="/cart"><i class="icon-cart"></i></a>',
        "selector": "a.cart-link",
        "expected_severity": "High",
        "description": "Link with only icon, no accessible name"
    },
]

# Test cases for code fix generation
CODE_FIX_TESTS = [
    {
        "id": "fix-image-alt",
        "rule_id": "image-alt",
        "html": '<img src="product.jpg" class="product-image">',
        "context": "E-commerce product page showing a laptop",
        "expected_contains": ["alt=", "laptop"],
    },
    {
        "id": "fix-form-label",
        "rule_id": "label",
        "html": '<input type="text" id="search" placeholder="Search...">',
        "context": "Search form in website header",
        "expected_contains": ["<label", "for=", "search"],
    },
    {
        "id": "fix-button-name",
        "rule_id": "button-name",
        "html": '<button onclick="submit()"><i class="fa-check"></i></button>',
        "context": "Form submission button",
        "expected_contains": ["aria-label", "submit"],
    },
]

# Models that use "thinking mode" and need higher token limits
THINKING_MODELS = ["gemini-2.5", "gemini-3"]


@dataclass
class TextBenchmarkResult:
    model: str
    test_id: str
    task_type: str  # classification, code_fix, summary
    success: bool
    inference_time_ms: float
    accuracy: float  # 0-1 based on expected output
    response_preview: str
    error: Optional[str] = None


def run_gemini_text(model: str, prompt: str, api_key: str, max_tokens: int = 500) -> tuple[str, float]:
    """Run Gemini text model inference."""
    # Models with thinking mode need higher token limits
    is_thinking_model = any(t in model for t in THINKING_MODELS)
    if is_thinking_model:
        max_tokens = max(max_tokens, 2000)

    start_time = time.perf_counter()

    try:
        response = httpx.post(
            f"{GEMINI_API_BASE}/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": max_tokens,
                }
            },
            timeout=120.0
        )

        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000

        if response.status_code != 200:
            error_detail = response.text[:500]
            return f"ERROR: {response.status_code} - {error_detail}", elapsed_ms

        data = response.json()
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


def benchmark_classification(model: str, api_key: str) -> List[TextBenchmarkResult]:
    """Benchmark accessibility issue classification."""
    results = []

    for test in CLASSIFICATION_TESTS:
        prompt = f"""You are an accessibility expert. Classify the severity of this WCAG violation.

Rule: {test['rule_id']}
Impact: {test['impact']}
HTML: {test['html']}
Selector: {test['selector']}

Respond ONLY with valid JSON:
{{
  "severity": "Critical|High|Medium|Low",
  "explanation": "Brief explanation",
  "business_impact": "Legal/business risk"
}}"""

        print(f"    Testing {test['id']}...", end=" ", flush=True)
        response, elapsed_ms = run_gemini_text(model, prompt, api_key, max_tokens=300)
        time.sleep(2)  # Rate limit buffer

        if response.startswith("ERROR:"):
            print(f"ERROR ({elapsed_ms:.0f}ms)")
            results.append(TextBenchmarkResult(
                model=model,
                test_id=test["id"],
                task_type="classification",
                success=False,
                inference_time_ms=elapsed_ms,
                accuracy=0.0,
                response_preview=response[:200],
                error=response
            ))
            continue

        # Check if response contains expected severity
        response_upper = response.upper()
        expected = test["expected_severity"].upper()
        accuracy = 1.0 if expected in response_upper else 0.0

        print(f"{'✓' if accuracy > 0 else '✗'} ({elapsed_ms:.0f}ms)")

        results.append(TextBenchmarkResult(
            model=model,
            test_id=test["id"],
            task_type="classification",
            success=True,
            inference_time_ms=elapsed_ms,
            accuracy=accuracy,
            response_preview=response[:200],
            error=None
        ))

    return results


def benchmark_code_fix(model: str, api_key: str) -> List[TextBenchmarkResult]:
    """Benchmark accessibility code fix generation."""
    results = []

    for test in CODE_FIX_TESTS:
        prompt = f"""You are an accessibility expert. Fix this WCAG violation.

Rule: {test['rule_id']}
Current HTML: {test['html']}
Context: {test['context']}

Provide the fixed HTML code that resolves the accessibility issue.
Respond with ONLY the fixed HTML code, no explanations."""

        print(f"    Testing {test['id']}...", end=" ", flush=True)
        response, elapsed_ms = run_gemini_text(model, prompt, api_key, max_tokens=500)
        time.sleep(2)  # Rate limit buffer

        if response.startswith("ERROR:"):
            print(f"ERROR ({elapsed_ms:.0f}ms)")
            results.append(TextBenchmarkResult(
                model=model,
                test_id=test["id"],
                task_type="code_fix",
                success=False,
                inference_time_ms=elapsed_ms,
                accuracy=0.0,
                response_preview=response[:200],
                error=response
            ))
            continue

        # Check if response contains expected elements
        response_lower = response.lower()
        matches = sum(1 for exp in test["expected_contains"] if exp.lower() in response_lower)
        accuracy = matches / len(test["expected_contains"])

        print(f"{accuracy:.0%} ({elapsed_ms:.0f}ms)")

        results.append(TextBenchmarkResult(
            model=model,
            test_id=test["id"],
            task_type="code_fix",
            success=True,
            inference_time_ms=elapsed_ms,
            accuracy=accuracy,
            response_preview=response[:200],
            error=None
        ))

    return results


def benchmark_summary(model: str, api_key: str) -> List[TextBenchmarkResult]:
    """Benchmark scan summary generation."""
    results = []

    # Single summary test with sample scan data
    scan_data = {
        "url": "https://example.com",
        "total_issues": 15,
        "critical": 3,
        "high": 5,
        "medium": 4,
        "low": 3,
        "top_issues": [
            "Missing alt text on 3 images",
            "Low color contrast on navigation",
            "Form inputs without labels"
        ]
    }

    prompt = f"""Summarize this accessibility scan for a business executive.

URL: {scan_data['url']}
Total Issues: {scan_data['total_issues']}
Critical: {scan_data['critical']}, High: {scan_data['high']}, Medium: {scan_data['medium']}, Low: {scan_data['low']}

Top Issues:
- {scan_data['top_issues'][0]}
- {scan_data['top_issues'][1]}
- {scan_data['top_issues'][2]}

Provide a 2-3 paragraph summary covering:
1. Overall compliance status and risk level
2. Top priorities to fix
3. Recommended next steps

Use business language, not technical jargon."""

    print(f"    Testing summary...", end=" ", flush=True)
    response, elapsed_ms = run_gemini_text(model, prompt, api_key, max_tokens=600)

    if response.startswith("ERROR:"):
        print(f"ERROR ({elapsed_ms:.0f}ms)")
        results.append(TextBenchmarkResult(
            model=model,
            test_id="scan-summary",
            task_type="summary",
            success=False,
            inference_time_ms=elapsed_ms,
            accuracy=0.0,
            response_preview=response[:200],
            error=response
        ))
    else:
        # Check for key business terms
        expected_terms = ["risk", "compliance", "fix", "priority", "recommend"]
        response_lower = response.lower()
        matches = sum(1 for term in expected_terms if term in response_lower)
        accuracy = matches / len(expected_terms)

        print(f"{accuracy:.0%} ({elapsed_ms:.0f}ms)")

        results.append(TextBenchmarkResult(
            model=model,
            test_id="scan-summary",
            task_type="summary",
            success=True,
            inference_time_ms=elapsed_ms,
            accuracy=accuracy,
            response_preview=response[:200],
            error=None
        ))

    return results


def print_results(all_results: Dict[str, List[TextBenchmarkResult]]):
    """Print formatted benchmark results."""
    print("\n" + "=" * 80)
    print(" GEMINI TEXT MODEL BENCHMARK RESULTS")
    print("=" * 80)

    for model, results in all_results.items():
        print(f"\n## {model}")
        print("-" * 60)

        successful = [r for r in results if r.success]
        if not successful:
            print("  No successful tests!")
            continue

        avg_time = sum(r.inference_time_ms for r in successful) / len(successful)
        avg_accuracy = sum(r.accuracy for r in successful) / len(successful)
        success_rate = len(successful) / len(results)

        print(f"  Avg Time: {avg_time:.0f}ms | Accuracy: {avg_accuracy:.0%} | Success: {success_rate:.0%}")
        print()

        # Results by task type
        task_types = set(r.task_type for r in results)
        for task in sorted(task_types):
            task_results = [r for r in results if r.task_type == task and r.success]
            if task_results:
                task_accuracy = sum(r.accuracy for r in task_results) / len(task_results)
                task_time = sum(r.inference_time_ms for r in task_results) / len(task_results)
                print(f"    {task}: {task_accuracy:.0%} accuracy, {task_time:.0f}ms avg")


def main():
    parser = argparse.ArgumentParser(description="Gemini Text Model Benchmark for Aelira")
    parser.add_argument("--model", type=str, help="Test specific model only")
    parser.add_argument("--api-key", type=str, help="Gemini API key")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    parser.add_argument("--all-models", action="store_true", help="Test all available models")
    args = parser.parse_args()

    # Get API key
    api_key = args.api_key or GEMINI_API_KEY
    if not api_key:
        print("ERROR: No API key provided. Set GEMINI_API_KEY or use --api-key")
        return

    # Determine models to test
    if args.all_models:
        models_to_test = GEMINI_MODELS
    elif args.model:
        models_to_test = [args.model]
    else:
        models_to_test = ["gemini-2.0-flash"]  # Default

    print(f"Testing models: {', '.join(models_to_test)}")

    # Run benchmarks
    all_results = {}
    for model in models_to_test:
        print(f"\n  Benchmarking {model}...")

        results = []

        print("  Classification tests:")
        results.extend(benchmark_classification(model, api_key))

        print("  Code fix tests:")
        results.extend(benchmark_code_fix(model, api_key))

        print("  Summary tests:")
        results.extend(benchmark_summary(model, api_key))

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
