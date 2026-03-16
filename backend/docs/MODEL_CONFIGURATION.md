# Aelira Model Configuration Guide

This guide helps open source users configure LLM models for optimal performance on their hardware.

## Quick Start

Aelira uses [Ollama](https://ollama.ai) for local LLM inference. By default, it uses the **recommended** profile which balances speed and accuracy.

```bash
# Pull required models (recommended profile)
ollama pull qwen2.5-coder:1.5b  # ~1GB - Fast classification
ollama pull qwen2.5-coder:3b    # ~2GB - Accurate code generation
```

## Model Profiles

Aelira supports 4 model profiles optimized for different hardware configurations:

| Profile | RAM Required | Speed | Accuracy | Best For |
|---------|-------------|-------|----------|----------|
| `minimal` | 4GB+ | ⚡⚡⚡ | Good | Low-end hardware, Raspberry Pi |
| `recommended` | 8GB+ | ⚡⚡ | Excellent | Most users, VPS, laptops |
| `performance` | 16GB+ | ⚡ | Best | Servers, workstations |
| `legacy` | 32GB+ | 🐢 | Good | Original config (not recommended) |

### Profile Details

#### Minimal Profile
- **Classifier:** `qwen2.5-coder:1.5b`
- **Code Generator:** `qwen2.5-coder:1.5b`
- **Total RAM:** ~1GB
- **Avg Response Time:** ~3 seconds

Best for: Raspberry Pi 4/5, low-end VPS (2GB RAM), older laptops.

```bash
# Pull models
ollama pull qwen2.5-coder:1.5b

# Set profile
export AELIRA_MODEL_PROFILE=minimal
```

#### Recommended Profile (Default)
- **Classifier:** `qwen2.5-coder:1.5b` (fast initial classification)
- **Code Generator:** `qwen2.5-coder:3b` (100% code generation accuracy!)
- **Total RAM:** ~3GB
- **Avg Response Time:** ~5 seconds

Best for: Most users, 8GB+ RAM laptops, standard VPS (4GB+ RAM).

```bash
# Pull models
ollama pull qwen2.5-coder:1.5b
ollama pull qwen2.5-coder:3b

# Set profile (optional - this is the default)
export AELIRA_MODEL_PROFILE=recommended
```

#### Performance Profile
- **Classifier:** `qwen2.5-coder:3b`
- **Code Generator:** `qwen2.5-coder:3b`
- **Total RAM:** ~2GB (single model)
- **Avg Response Time:** ~5-6 seconds

Best for: Servers, workstations, 16GB+ RAM systems. Uses the same model for both tasks, which means slower classification but more consistent results.

```bash
# Pull models
ollama pull qwen2.5-coder:3b

# Set profile
export AELIRA_MODEL_PROFILE=performance
```

#### Legacy Profile (Not Recommended)
- **Classifier:** `llama3.2:3b`
- **Code Generator:** `qwen2.5-coder:7b`
- **Total RAM:** ~7GB
- **Avg Response Time:** ~12 seconds

⚠️ **Not recommended for CPU-only systems.** The 7B model is too slow for practical use without a GPU.

```bash
# Pull models (only if you have a GPU)
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:7b

# Set profile
export AELIRA_MODEL_PROFILE=legacy
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AELIRA_MODEL_PROFILE` | `recommended` | Profile name (minimal, recommended, performance, legacy) |
| `AELIRA_CLASSIFIER_MODEL` | (from profile) | Override classifier model |
| `AELIRA_CODER_MODEL` | (from profile) | Override code generation model |

### Docker Compose Configuration

Add environment variables to your `docker-compose.yml`:

```yaml
services:
  api:
    environment:
      AELIRA_MODEL_PROFILE: recommended
      # Or override specific models:
      # AELIRA_CLASSIFIER_MODEL: qwen2.5-coder:1.5b
      # AELIRA_CODER_MODEL: qwen2.5-coder:3b
```

### Custom Model Configuration

You can mix and match models by setting individual environment variables:

```bash
# Use fast 1.5B for classification, accurate 3B for code generation
export AELIRA_CLASSIFIER_MODEL=qwen2.5-coder:1.5b
export AELIRA_CODER_MODEL=qwen2.5-coder:3b
```

## Benchmark Results

These benchmarks were run on a 4-core CPU with 32GB RAM (no GPU), testing real WCAG accessibility tasks:

| Model | Avg Time | Accuracy | Code Gen | Classification | Memory |
|-------|----------|----------|----------|----------------|--------|
| qwen2.5-coder:1.5b | 3.0s | 76.5% | 80% | 60% | ~1GB |
| qwen2.5-coder:3b | 5.5s | 76.0% | **100%** | 60% | ~2GB |
| llama3.2:3b | 5.6s | 76.5% | 60% | 80% | ~2GB |
| qwen2.5-coder:7b | 11.7s | 75.0% | 80% | 80% | ~5GB |

### Key Findings

1. **qwen2.5-coder:3b achieves 100% code generation accuracy** - This is why it's used for code fixes in the recommended profile.

2. **qwen2.5-coder:1.5b is the fastest** (~3 seconds) while maintaining good accuracy - Perfect for initial classification.

3. **7B models are too slow for CPU** - 12+ seconds per request is not practical for bulk scanning.

4. **Qwen 2.5 Coder outperforms Llama 3.2** for accessibility code fixes - Qwen's code-specific training makes it better at generating valid HTML/CSS fixes.

5. **Qwen3 base models have thinking mode issues** - Use `qwen3:4b-instruct` instead of `qwen3:4b`. However, `qwen3:4b-instruct` is slower (~16s) than `qwen2.5-coder:3b` (~5s) for similar accuracy.

## Hardware Recommendations

### Minimum Requirements
- **CPU:** 2 cores
- **RAM:** 4GB
- **Storage:** 5GB for models
- **Profile:** `minimal`

### Recommended Requirements
- **CPU:** 4 cores
- **RAM:** 8GB
- **Storage:** 10GB for models
- **Profile:** `recommended`

### Optimal Requirements
- **CPU:** 8+ cores
- **RAM:** 16GB+
- **Storage:** 20GB for models
- **GPU:** Optional (NVIDIA with CUDA for faster inference)
- **Profile:** `performance`

## GPU Acceleration

If you have an NVIDIA GPU with CUDA support, Ollama will automatically use it for faster inference.

```bash
# Check if Ollama is using GPU
ollama run qwen2.5-coder:3b "test"
# Look for "GPU" in the output

# For Docker, use nvidia-docker
docker run --gpus all -v ollama:/root/.ollama -p 11434:11434 ollama/ollama
```

With GPU acceleration:
- 7B models become practical (~2-3 seconds)
- You can use larger models like `qwen2.5-coder:14b` for even better accuracy

## Troubleshooting

### Model Not Found

```bash
# Pull the required model
ollama pull qwen2.5-coder:3b

# Verify it's available
ollama list
```

### Out of Memory

```bash
# Switch to minimal profile
export AELIRA_MODEL_PROFILE=minimal

# Or use smaller models
export AELIRA_CLASSIFIER_MODEL=qwen2.5-coder:0.5b
export AELIRA_CODER_MODEL=qwen2.5-coder:1.5b
```

### Slow Response Times

1. Check if another model is loaded in memory:
   ```bash
   # Ollama keeps models in memory - loading a new model may take time
   ollama ps
   ```

2. Use the minimal profile for faster responses
3. Consider adding more RAM or using GPU acceleration

### Empty Responses from Qwen3

Qwen3 base models (e.g., `qwen3:4b`) use a "thinking mode" by default that outputs to a separate `thinking` field, leaving the main response empty. **This cannot be reliably disabled via prompts.**

**Solution: Use the `-instruct` variant instead:**

```bash
# ❌ DON'T use base qwen3 (has thinking mode issues)
ollama pull qwen3:4b

# ✅ DO use the instruct variant (no thinking mode)
ollama pull qwen3:4b-instruct
```

Available instruct variants:
- `qwen3:4b-instruct` (~2.5GB)
- `qwen3:8b-instruct` (~5GB)
- `qwen3:14b-instruct` (~9GB)

The instruct variants work correctly without any `/no_think` prefix needed.

## Vision Models (Image Alt-Text Generation)

Aelira uses vision models for automatic alt-text generation. These models analyze images and generate accessibility descriptions.

### Vision Model Comparison

| Model | Type | Avg Time | Accuracy | Icons | Photos | Cost |
|-------|------|----------|----------|-------|--------|------|
| **Gemini 2.5 Flash** | Cloud API | ~3.5s | **61%** | 59% | 75% | Paid |
| Gemini 3 Pro Preview | Cloud API | ~9.8s | 60% | 60% | - | Paid |
| **Gemini 2.0 Flash-Lite** | Cloud API | **~1.7s** | 57% | 54% | 75% | Free |
| Gemini 2.0 Flash | Cloud API | ~3.9s | 52% | 48% | 75% | Free |
| Gemini 2.5 Pro | Cloud API | ~10s | 49% | 53% | 25% | Paid |
| LLaVA 7B | Local | ~49s | 39% | 41% | 25% | Self-hosted |
| Moondream | Local | ~15s | 4% | 0% | 25% | Self-hosted |

### Recommended: Gemini 2.5 Flash (Paid Tier)

For production with billing enabled, **Gemini 2.5 Flash** provides the best accuracy:

**Pros:**
- ✅ **61% accuracy** (best overall for icons)
- ✅ **~3.5 second response time**
- ✅ **Excellent icon descriptions** (59% accuracy)
- ✅ **High-quality, concise text** (suitable for screen readers)

### Alternative: Gemini 2.0 Flash-Lite (Free Tier)

For free tier or cost-sensitive deployments:

**Pros:**
- ✅ **57% accuracy** (nearly as good as paid models)
- ✅ **Fastest at ~1.7 seconds** (best speed)
- ✅ **Free tier available**
- ✅ **No thinking mode overhead**

**Performance by Image Type (Nov 2025 Benchmark):**

| Image Type | Gemini 2.5 Flash | Gemini 2.0 Flash-Lite | LLaVA 7B |
|------------|------------------|----------------------|----------|
| Photos | **75%** | 75% | 25% |
| User Icons | **86%** | 86% | 29% |
| Warning Signs | **67%** | 67% | 67% |
| Email Icons | **80%** | 60% | 60% |
| Checkmarks | **71%** | 43% | 57% |
| Documents | 50% | **67%** | 33% |
| Accessibility Icons | 0% | 0% | 0% |

**Sample Responses (Gemini 2.0 Flash):**
- Pineapple: "A pineapple sits in green grass against a bright blue sky with scattered white clouds."
- User icon: "A solid black silhouette of a person icon. It consists of a circle representing the head and a rounded shape representing the body and shoulders."
- Email: "A black outline of a classic email envelope icon on a white background."

### Setup: Gemini API

```bash
# 1. Get API key from Google AI Studio (free)
# https://aistudio.google.com/apikey

# 2. Set environment variable
export GEMINI_API_KEY="your-api-key-here"
```

**Free Tier Limits (as of Nov 2025):**
- gemini-2.0-flash: Available on free tier
- gemini-2.0-flash-lite: Available on free tier
- gemini-3-pro-*, gemini-2.5-*: Require paid tier (limit: 0 on free)

### Local Vision Models (Fallback)

For air-gapped or privacy-sensitive deployments:

#### LLaVA 7B (Local - Recommended)

```bash
ollama pull llava:7b
```

| Metric | Value |
|--------|-------|
| RAM Required | ~4.7GB |
| Avg Time (CPU) | ~49s |
| Avg Time (GPU) | ~5-10s |
| Overall Accuracy | 39% |
| Best For | Warning signs, email icons, checkmarks |

**Known Issues:**
- Photos: Often identifies subject but misses context keywords
- Accessibility icons: Consistent hallucinations
- Abstract symbols: Misinterpretation common

#### Moondream (NOT Recommended)

```bash
ollama pull moondream
```

**Status: DO NOT USE for production**
- 4% accuracy on benchmark
- High hallucination rate (describes "iced tea" for pineapple, "urn" for user icon)
- Only suitable for very basic test cases

### Vision Model Recommendations

**For Production (Recommended):**

1. **Gemini 2.0 Flash** via Cloud API
   - 52% accuracy, ~2s response time
   - Free tier available
   - Best for: photos, user icons, common web icons

**For Privacy-Sensitive Deployments:**

2. **LLaVA 7B** (local, requires GPU for practical speed)
   - 39% accuracy, ~49s on CPU / ~5-10s on GPU
   - Good for: warning signs, checkmarks, email icons
   - Requires human review

**For Maximum Accuracy:**

3. **Human-authored alt-text** with AI suggestions
   - Use Gemini to generate draft alt-text
   - Require human review before publishing
   - Essential for WCAG compliance

**Known Limitations (All Models):**
- Accessibility wheelchair icons: All models hallucinate
- Abstract symbols: May be misinterpreted
- Always require human review for WCAG compliance

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | (required) | Google Gemini API key |
| `AELIRA_VISION_MODEL` | `gemini-2.0-flash` | Vision model for alt-text |
| `AELIRA_VISION_FALLBACK` | `llava:7b` | Fallback model if API fails |
| `AELIRA_VISION_REQUIRE_REVIEW` | `true` | Require human review |

## Running the Benchmarks

### LLM Benchmark (Text Models)

```bash
cd backend
python -m benchmarks.llm_benchmark --models qwen2.5-coder:1.5b qwen2.5-coder:3b
```

### Vision Benchmark (Local Models)

```bash
cd backend
python benchmarks/vision_benchmark.py --output benchmarks/vision_results.json
```

### Vision Benchmark (Gemini Cloud API)

```bash
cd backend
export GEMINI_API_KEY="your-api-key"
python benchmarks/gemini_vision_benchmark.py --all-models --output benchmarks/vision_results_gemini.json
```

## Model Comparison Chart

```
Speed vs Accuracy Tradeoff:

Fast ←――――――――――――――――――――――――――――――→ Accurate

qwen2.5-coder:0.5b  qwen2.5-coder:1.5b  qwen2.5-coder:3b  qwen2.5-coder:7b
       ↓                    ↓                   ↓                 ↓
    ~1.5s                 ~3s                 ~5s              ~12s
    ~70%                 ~76%                ~76%              ~75%

                    RECOMMENDED →  ←  FOR CODE GENERATION
```

## Questions?

- [GitHub Issues](https://github.com/aelira-ai/aelira/issues)
- [Discord Community](https://discord.gg/aelira)
- [Documentation](https://docs.aelira.ai)
