#!/usr/bin/env python3
"""
LLM Model Benchmark Suite for Aelira

Tests models on real WCAG accessibility tasks to measure:
1. Speed (tokens/second, total inference time)
2. Accuracy (correct classification, valid code generation)
3. Memory usage

Designed to run in Docker environment mirroring production.

Usage:
    python benchmarks/llm_benchmark.py --all
    python benchmarks/llm_benchmark.py --model qwen3:4b
    python benchmarks/llm_benchmark.py --task classification
"""

import argparse
import json
import time
import statistics
import subprocess
import sys
from dataclasses import dataclass, asdict
from typing import Optional
import httpx

# Ollama API endpoint
OLLAMA_HOST = "http://localhost:11434"

# Models that require thinking mode to be disabled
# These models use <think>...</think> tags by default which can cause empty responses
#
# IMPORTANT (Jan 2026): Base qwen3 models (qwen3:4b, qwen3:8b) output ALL content
# to a separate 'thinking' field in the API response, resulting in empty responses.
# The /no_think prefix does NOT fix this. Use '-instruct' variants instead:
#   - qwen3:4b-instruct (works correctly)
#   - qwen3:8b-instruct (works correctly)
THINKING_MODE_MODELS = ["qwen3", "deepseek-r1"]

import re

def requires_no_think(model_name: str) -> bool:
    """Check if a model requires thinking mode to be disabled."""
    model_lower = model_name.lower()
    return any(thinking_model in model_lower for thinking_model in THINKING_MODE_MODELS)

def clean_thinking_response(content: str) -> str:
    """Remove thinking mode artifacts from response."""
    # Remove empty thinking tags
    content = re.sub(r'<think>\s*</think>', '', content)
    # Remove thinking tags with content
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    return content.strip()

# Test cases for WCAG accessibility tasks
CLASSIFICATION_TESTS = [
    {
        "id": "img-alt-missing",
        "violation": {
            "rule_id": "image-alt",
            "description": "Images must have alternate text",
            "html": '<img src="logo.png" class="header-logo">',
            "impact": "critical"
        },
        "expected_severity": "critical",
        "expected_contains": ["alt", "screen reader", "accessibility"]
    },
    {
        "id": "color-contrast-fail",
        "violation": {
            "rule_id": "color-contrast",
            "description": "Elements must have sufficient color contrast",
            "html": '<p style="color: #777; background: #fff;">Low contrast text</p>',
            "impact": "serious"
        },
        "expected_severity": "serious",
        "expected_contains": ["contrast", "4.5:1", "WCAG"]
    },
    {
        "id": "heading-order",
        "violation": {
            "rule_id": "heading-order",
            "description": "Heading levels should increase by one",
            "html": '<h1>Title</h1><h3>Skipped h2</h3>',
            "impact": "moderate"
        },
        "expected_severity": "moderate",
        "expected_contains": ["heading", "h2", "structure"]
    },
    {
        "id": "link-name-missing",
        "violation": {
            "rule_id": "link-name",
            "description": "Links must have discernible text",
            "html": '<a href="/page"><img src="icon.png"></a>',
            "impact": "serious"
        },
        "expected_severity": "serious",
        "expected_contains": ["link", "text", "accessible name"]
    },
    {
        "id": "label-missing",
        "violation": {
            "rule_id": "label",
            "description": "Form elements must have labels",
            "html": '<input type="text" name="email" placeholder="Email">',
            "impact": "critical"
        },
        "expected_severity": "critical",
        "expected_contains": ["label", "form", "input"]
    }
]

CODE_FIX_TESTS = [
    {
        "id": "fix-img-alt",
        "violation": {
            "rule_id": "image-alt",
            "html": '<img src="team-photo.jpg" class="about-image">'
        },
        "expected_contains": ['alt="', "<img"],
        "must_be_valid_html": True
    },
    {
        "id": "fix-color-contrast",
        "violation": {
            "rule_id": "color-contrast",
            "html": '<button style="color: #888; background: #ccc;">Submit</button>'
        },
        "expected_contains": ["color:", "background"],
        "must_be_valid_html": True
    },
    {
        "id": "fix-link-name",
        "violation": {
            "rule_id": "link-name",
            "html": '<a href="/download"><i class="icon-download"></i></a>'
        },
        "expected_contains": ["aria-label", "<a"],
        "must_be_valid_html": True
    },
    {
        "id": "fix-heading-order",
        "violation": {
            "rule_id": "heading-order",
            "html": '<h1>Welcome</h1><h4>Features</h4>'
        },
        "expected_contains": ["<h2", "</h2>"],
        "must_be_valid_html": True
    },
    {
        "id": "fix-form-label",
        "violation": {
            "rule_id": "label",
            "html": '<input type="email" id="email" placeholder="Enter email">'
        },
        "expected_contains": ["<label", "for="],
        "must_be_valid_html": True
    }
]

IMAGE_DESCRIPTION_TESTS = [
    {
        "id": "describe-chart",
        "prompt": "Describe this image for a screen reader user. The image shows a bar chart comparing quarterly sales figures.",
        "expected_contains": ["chart", "sales", "bar"],
        "max_length": 200
    },
    {
        "id": "describe-team-photo",
        "prompt": "Generate alt text for an image showing a group of 5 people in business attire standing in front of a university building.",
        "expected_contains": ["people", "university", "building"],
        "max_length": 150
    },
    {
        "id": "describe-diagram",
        "prompt": "Create accessible alt text for a flowchart showing the user registration process with 4 steps.",
        "expected_contains": ["flowchart", "registration", "steps"],
        "max_length": 200
    }
]


@dataclass
class BenchmarkResult:
    model: str
    task_type: str
    test_id: str
    success: bool
    inference_time_ms: float
    tokens_generated: int
    tokens_per_second: float
    accuracy_score: float
    error: Optional[str] = None
    response_preview: Optional[str] = None


def check_ollama_available() -> bool:
    """Check if Ollama is running and accessible."""
    try:
        response = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
        return response.status_code == 200
    except Exception:
        return False


def get_installed_models() -> list[str]:
    """Get list of installed Ollama models."""
    try:
        response = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
        data = response.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def pull_model(model: str) -> bool:
    """Pull a model if not already installed."""
    print(f"  Pulling {model}...")
    try:
        # Use streaming pull to show progress
        with httpx.stream(
            "POST",
            f"{OLLAMA_HOST}/api/pull",
            json={"name": model},
            timeout=600.0
        ) as response:
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    if "status" in data:
                        status = data["status"]
                        if "pulling" in status or "downloading" in status:
                            percent = data.get("completed", 0) / max(data.get("total", 1), 1) * 100
                            print(f"\r    {status}: {percent:.1f}%", end="", flush=True)
                        elif status == "success":
                            print(f"\r    {model} pulled successfully!          ")
                            return True
        return True
    except Exception as e:
        print(f"\n    Error pulling {model}: {e}")
        return False


def run_inference(model: str, prompt: str, system: str = "", temperature: float = 0.3) -> tuple[str, float, int]:
    """
    Run inference and return (response, time_ms, token_count).
    Automatically handles thinking mode for Qwen3/DeepSeek-R1 models.
    """
    # Prepend /no_think for thinking-mode models
    actual_prompt = prompt
    if requires_no_think(model):
        actual_prompt = f"/no_think\n\n{prompt}"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": actual_prompt})

    start_time = time.perf_counter()

    try:
        response = httpx.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": 500
                }
            },
            timeout=120.0
        )
        response.raise_for_status()
        data = response.json()

        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000

        content = data.get("message", {}).get("content", "")

        # Clean any thinking mode artifacts
        content = clean_thinking_response(content)

        # Estimate tokens (rough: ~4 chars per token)
        token_count = len(content) // 4 + 1

        return content, elapsed_ms, token_count

    except Exception as e:
        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000
        return f"ERROR: {e}", elapsed_ms, 0


def benchmark_classification(model: str) -> list[BenchmarkResult]:
    """Benchmark model on WCAG violation classification tasks."""
    results = []

    system_prompt = """You are a WCAG 2.1 accessibility expert. Analyze the given HTML violation and:
1. Classify its severity (critical, serious, moderate, minor)
2. Explain why this is an accessibility issue
3. Cite the relevant WCAG success criterion

Be concise and accurate."""

    for test in CLASSIFICATION_TESTS:
        prompt = f"""Analyze this accessibility violation:

Rule: {test['violation']['rule_id']}
Description: {test['violation']['description']}
HTML: {test['violation']['html']}

Classify the severity and explain the issue."""

        response, time_ms, tokens = run_inference(model, prompt, system_prompt)

        # Calculate accuracy
        accuracy = 0.0
        response_lower = response.lower()

        # Check severity classification (40% weight)
        if test["expected_severity"] in response_lower:
            accuracy += 0.4

        # Check for expected keywords (60% weight, divided among keywords)
        keyword_weight = 0.6 / len(test["expected_contains"])
        for keyword in test["expected_contains"]:
            if keyword.lower() in response_lower:
                accuracy += keyword_weight

        tokens_per_sec = (tokens / time_ms * 1000) if time_ms > 0 else 0

        results.append(BenchmarkResult(
            model=model,
            task_type="classification",
            test_id=test["id"],
            success=accuracy >= 0.6,
            inference_time_ms=time_ms,
            tokens_generated=tokens,
            tokens_per_second=tokens_per_sec,
            accuracy_score=accuracy,
            response_preview=response[:200] if not response.startswith("ERROR") else response
        ))

    return results


def benchmark_code_generation(model: str) -> list[BenchmarkResult]:
    """Benchmark model on WCAG code fix generation tasks."""
    results = []

    system_prompt = """You are a web accessibility expert. Generate a fixed version of the HTML that resolves the WCAG violation.

Rules:
- Output ONLY the fixed HTML code
- Do not include explanations
- Ensure the fix is valid HTML
- Make minimal changes to fix the issue"""

    for test in CODE_FIX_TESTS:
        prompt = f"""Fix this accessibility violation:

Rule: {test['violation']['rule_id']}
Original HTML: {test['violation']['html']}

Output the fixed HTML:"""

        response, time_ms, tokens = run_inference(model, prompt, system_prompt, temperature=0.2)

        # Calculate accuracy
        accuracy = 0.0
        response_clean = response.strip()

        # Check for expected fix patterns (70% weight)
        pattern_weight = 0.7 / len(test["expected_contains"])
        for pattern in test["expected_contains"]:
            if pattern.lower() in response_clean.lower():
                accuracy += pattern_weight

        # Check if it looks like valid HTML (30% weight)
        if "<" in response_clean and ">" in response_clean:
            accuracy += 0.15
            # Check for balanced tags
            if response_clean.count("<") == response_clean.count(">"):
                accuracy += 0.15

        tokens_per_sec = (tokens / time_ms * 1000) if time_ms > 0 else 0

        results.append(BenchmarkResult(
            model=model,
            task_type="code_generation",
            test_id=test["id"],
            success=accuracy >= 0.7,
            inference_time_ms=time_ms,
            tokens_generated=tokens,
            tokens_per_second=tokens_per_sec,
            accuracy_score=accuracy,
            response_preview=response[:300] if not response.startswith("ERROR") else response
        ))

    return results


def benchmark_vision(model: str) -> list[BenchmarkResult]:
    """Benchmark vision model on image description tasks (text-only simulation)."""
    results = []

    system_prompt = """You are an accessibility expert generating alt text for images.
Create concise, descriptive alt text that conveys the essential information.
Keep responses under 150 characters when possible."""

    for test in IMAGE_DESCRIPTION_TESTS:
        response, time_ms, tokens = run_inference(model, test["prompt"], system_prompt)

        # Calculate accuracy
        accuracy = 0.0
        response_lower = response.lower()

        # Check for expected content (80% weight)
        keyword_weight = 0.8 / len(test["expected_contains"])
        for keyword in test["expected_contains"]:
            if keyword.lower() in response_lower:
                accuracy += keyword_weight

        # Check length appropriateness (20% weight)
        if len(response) <= test["max_length"]:
            accuracy += 0.2

        tokens_per_sec = (tokens / time_ms * 1000) if time_ms > 0 else 0

        results.append(BenchmarkResult(
            model=model,
            task_type="vision",
            test_id=test["id"],
            success=accuracy >= 0.6,
            inference_time_ms=time_ms,
            tokens_generated=tokens,
            tokens_per_second=tokens_per_sec,
            accuracy_score=accuracy,
            response_preview=response[:200] if not response.startswith("ERROR") else response
        ))

    return results


def warm_up_model(model: str):
    """Run a warm-up inference to load the model into memory."""
    print(f"  Warming up {model}...")
    run_inference(model, "Hello, respond with just 'ready'.", temperature=0.0)
    print(f"  {model} ready.")


def print_results_table(results: list[BenchmarkResult], title: str):
    """Print results in a formatted table."""
    print(f"\n{'='*80}")
    print(f" {title}")
    print(f"{'='*80}")

    if not results:
        print("  No results.")
        return

    # Group by model
    models = {}
    for r in results:
        if r.model not in models:
            models[r.model] = []
        models[r.model].append(r)

    for model, model_results in models.items():
        print(f"\n  Model: {model}")
        print(f"  {'-'*70}")

        # Calculate aggregates
        avg_time = statistics.mean([r.inference_time_ms for r in model_results])
        avg_accuracy = statistics.mean([r.accuracy_score for r in model_results])
        avg_tps = statistics.mean([r.tokens_per_second for r in model_results if r.tokens_per_second > 0])
        success_rate = sum(1 for r in model_results if r.success) / len(model_results) * 100

        print(f"  Avg Time: {avg_time:.0f}ms | Avg Accuracy: {avg_accuracy:.1%} | "
              f"Tokens/s: {avg_tps:.1f} | Success: {success_rate:.0f}%")
        print()

        # Individual results
        for r in model_results:
            status = "✓" if r.success else "✗"
            print(f"    {status} {r.test_id}: {r.inference_time_ms:.0f}ms, "
                  f"accuracy={r.accuracy_score:.1%}, {r.tokens_per_second:.1f} tok/s")


def print_comparison_summary(all_results: dict[str, list[BenchmarkResult]]):
    """Print a comparison summary across all models."""
    print(f"\n{'='*80}")
    print(" COMPARISON SUMMARY")
    print(f"{'='*80}")

    # Aggregate by model
    model_stats = {}
    for task_type, results in all_results.items():
        for r in results:
            if r.model not in model_stats:
                model_stats[r.model] = {
                    "times": [],
                    "accuracies": [],
                    "tps": [],
                    "successes": 0,
                    "total": 0
                }
            model_stats[r.model]["times"].append(r.inference_time_ms)
            model_stats[r.model]["accuracies"].append(r.accuracy_score)
            if r.tokens_per_second > 0:
                model_stats[r.model]["tps"].append(r.tokens_per_second)
            model_stats[r.model]["total"] += 1
            if r.success:
                model_stats[r.model]["successes"] += 1

    print(f"\n  {'Model':<30} {'Avg Time':<12} {'Accuracy':<12} {'Tok/s':<10} {'Success':<10}")
    print(f"  {'-'*74}")

    for model, stats in sorted(model_stats.items()):
        avg_time = statistics.mean(stats["times"]) if stats["times"] else 0
        avg_acc = statistics.mean(stats["accuracies"]) if stats["accuracies"] else 0
        avg_tps = statistics.mean(stats["tps"]) if stats["tps"] else 0
        success_rate = stats["successes"] / stats["total"] * 100 if stats["total"] > 0 else 0

        print(f"  {model:<30} {avg_time:>8.0f}ms   {avg_acc:>8.1%}     {avg_tps:>6.1f}     {success_rate:>6.0f}%")

    # Recommend best model
    print(f"\n  {'='*74}")
    print("  RECOMMENDATIONS:")

    best_speed = min(model_stats.items(), key=lambda x: statistics.mean(x[1]["times"]))
    best_accuracy = max(model_stats.items(), key=lambda x: statistics.mean(x[1]["accuracies"]))
    best_balanced = max(model_stats.items(),
                        key=lambda x: statistics.mean(x[1]["accuracies"]) * 0.6 +
                                      (1 / (statistics.mean(x[1]["times"]) / 1000)) * 0.4)

    print(f"    Fastest:     {best_speed[0]}")
    print(f"    Most Accurate: {best_accuracy[0]}")
    print(f"    Best Balance:  {best_balanced[0]}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark LLM models for Aelira accessibility tasks")
    parser.add_argument("--model", "-m", type=str, help="Specific model to test")
    parser.add_argument("--task", "-t", choices=["classification", "code", "vision", "all"],
                        default="all", help="Task type to benchmark")
    parser.add_argument("--pull", "-p", action="store_true", help="Pull models if not installed")
    parser.add_argument("--all", "-a", action="store_true", help="Run all benchmarks")
    parser.add_argument("--output", "-o", type=str, help="Save results to JSON file")

    args = parser.parse_args()

    print("\n" + "="*80)
    print(" AELIRA LLM BENCHMARK SUITE")
    print(" Testing models for WCAG accessibility tasks")
    print("="*80)

    # Check Ollama
    if not check_ollama_available():
        print("\n❌ Ollama is not running. Please start it with:")
        print("   docker-compose -f docker-compose.dev.yml up -d ollama")
        sys.exit(1)

    print("\n✓ Ollama is running")

    # Determine models to test
    installed = get_installed_models()
    print(f"\nInstalled models: {', '.join(installed) if installed else 'None'}")

    # Default models to test for each task
    # Updated Jan 2026: Added Qwen3 series (April 2025) and other CPU-friendly models
    #
    # Model sizes for CPU planning:
    #   - qwen3:1.7b       ~1.2GB  (Q4_K_M)
    #   - qwen3:4b         ~2.8GB  (Q4_K_M) - rivals 72B quality
    #   - qwen2.5-coder:1.5b ~1.1GB
    #   - qwen2.5-coder:3b   ~2.0GB
    #   - smollm2:1.7b     ~1.1GB  (very fast)
    #   - deepseek-r1:1.5b ~1.1GB  (reasoning)
    #   - llama3.2:3b      ~2.0GB
    #
    CLASSIFICATION_MODELS = [
        "qwen2.5-coder:1.5b",  # Current baseline (60% accuracy, 4.2s)
        "qwen3:4b-instruct",   # New: Qwen3 (72% accuracy, 25.7s) - use -instruct!
        "smollm2:1.7b",        # New: HuggingFace, very fast
    ]
    CODE_MODELS = [
        "qwen2.5-coder:3b",    # Current baseline (100% accuracy) - BEST
        "qwen2.5-coder:1.5b",  # Smaller option
        "qwen3:4b-instruct",   # New: 86% accuracy - use -instruct!
    ]
    VISION_MODELS = ["moondream:latest", "minicpm-v:latest"]

    if args.model:
        models_to_test = [args.model]
    elif args.all:
        models_to_test = list(set(CLASSIFICATION_MODELS + CODE_MODELS + VISION_MODELS))
    else:
        # Use installed models
        models_to_test = installed if installed else ["qwen2.5-coder:3b"]

    # Pull missing models if requested
    if args.pull:
        for model in models_to_test:
            if model not in installed:
                pull_model(model)
                installed = get_installed_models()

    # Filter to only installed models
    models_to_test = [m for m in models_to_test if m in installed]

    if not models_to_test:
        print("\n❌ No models available to test. Use --pull to download models.")
        sys.exit(1)

    print(f"\nModels to test: {', '.join(models_to_test)}")

    all_results = {}

    # Run benchmarks
    for model in models_to_test:
        warm_up_model(model)

        if args.task in ["classification", "all"]:
            print(f"\n📊 Running classification benchmark for {model}...")
            results = benchmark_classification(model)
            all_results.setdefault("classification", []).extend(results)

        if args.task in ["code", "all"]:
            print(f"\n💻 Running code generation benchmark for {model}...")
            results = benchmark_code_generation(model)
            all_results.setdefault("code_generation", []).extend(results)

        if args.task in ["vision", "all"] and "vision" in model.lower() or "moondream" in model.lower() or "minicpm" in model.lower():
            print(f"\n🖼️  Running vision benchmark for {model}...")
            results = benchmark_vision(model)
            all_results.setdefault("vision", []).extend(results)

    # Print results
    for task_type, results in all_results.items():
        print_results_table(results, f"{task_type.upper()} RESULTS")

    print_comparison_summary(all_results)

    # Save to file if requested
    if args.output:
        output_data = {
            task: [asdict(r) for r in results]
            for task, results in all_results.items()
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\n✓ Results saved to {args.output}")


if __name__ == "__main__":
    main()
