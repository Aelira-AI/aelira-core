# Local AI models (Ollama)

> LMS use additionally enforces loopback-only Ollama, no API key, finite readiness probes, required models, and explicit admin purposes. See [LMS AI policy and provider readiness](lms-ai-policy.md).

How to run Aelira Core with fully local AI: which open models to use, what
hardware they need, and what each one does. With the Ollama provider,
document content never leaves your infrastructure.

**The scoring principle first:** AI never decides severity or compliance
scores in Aelira. Severity is computed by deterministic rules
(`src/ai/severity_rules.py`) and works even with no AI configured at all.
The models below generate *suggestions* — alt text, image descriptions,
issue explanations, and HTML/ARIA code fix snippets, all reviewable before
they're applied. A smaller local model means plainer prose and simpler code
suggestions, never different scores.

## What the AI lanes do

| Lane | Used for | Env var |
|---|---|---|
| **Vision** | Alt text for images, chart/diagram descriptions, OCR-assisted understanding | `OLLAMA_VISION_MODEL` |
| **Text** | Human-readable issue explanations and recommendations | `OLLAMA_TEXT_MODEL` |
| **Code** | Structural HTML/ARIA fix suggestions (headings, scope attributes, labels) | `OLLAMA_CODE_MODEL` |
| **Embeddings** | WCAG guideline retrieval, so explanations cite the right criteria | `OLLAMA_EMBEDDING_MODEL` |

Speech is always local and does not use Ollama: `faster-whisper` transcribes
audio/video for captions, `piper-tts` generates audio. They run on CPU.

## Tested defaults

These ship as the defaults and were benchmarked against accessibility tasks
(alt-text quality, ARIA correctness, explanation tone) in March 2026:

| Lane | Model | Download | Runs in | Why this one |
|---|---|---|---|---|
| Vision | `qwen2.5vl:3b` | ~3.2 GB | 8 GB RAM, CPU-capable | Strong OCR and chart understanding for its size; 125K context |
| Text | `gemma3:4b` | ~3.3 GB | 8 GB RAM, CPU-capable | Warm, faculty-friendly explanation tone |
| Code | `qwen2.5-coder:7b` | ~4.7 GB | 16 GB RAM or 8 GB VRAM | Best HTML structure, ARIA, and scope-attribute output in testing |
| Embeddings | `nomic-embed-text` | ~0.3 GB | anywhere | Standard, fast, good WCAG retrieval quality |

Select local generation with `LLM_PROVIDER=ollama`. Exact WCAG rule grounding
does not need embeddings. To add optional free-text semantic retrieval, also
set `EMBEDDING_PROVIDER=ollama`; only then will startup probe the configured
embedding model and generate missing vectors. It never downloads a model
implicitly, and an absent model does not stop startup.

```bash
ollama pull qwen2.5vl:3b && ollama pull gemma3:4b && ollama pull qwen2.5-coder:7b && ollama pull nomic-embed-text
```

The older `OLLAMA_FALLBACK_TEXT`, `OLLAMA_FALLBACK_CODE`, and
`OLLAMA_FALLBACK_VISION` names remain accepted for compatibility, but the
canonical `OLLAMA_*_MODEL` variables above take precedence.

## Hardware tiers

| Tier | Hardware | Configuration |
|---|---|---|
| **Minimum** | 8 GB RAM, no GPU | Set all three lanes to `gemma3:4b` (one 3.3 GB model in memory). Vision quality drops; everything works. |
| **Recommended** | 16 GB RAM, no GPU required | The tested defaults above. This is a spare desktop or a small VM. |
| **GPU workstation** | 12 GB+ VRAM (e.g. RTX 3060 12GB and up) | Upgrade vision to `qwen3-vl:8b` (as of mid-2026, the strongest open vision model at this size — native Ollama support, 256K context, leads document/chart benchmarks like DocVQA; ~12 GB at Q4). Keep the other lanes as-is or move code to `qwen2.5-coder:14b`. |
| **Department server** | 24 GB+ VRAM or multi-GPU | Larger open models (e.g. Qwen3-VL-30B-A3B, Gemma-family MoE releases) served via vLLM or any OpenAI-compatible endpoint — see below. |

Throughput note: a CPU-only deployment remediates a document in tens of
seconds rather than seconds. For batch jobs over large course archives, a
single mid-range GPU is the meaningful upgrade, not a bigger model.

## Using newer or bigger models

The model landscape moves fast; the defaults are pinned to what was tested,
not to what is newest. Two upgrade paths, no code changes:

1. **Any Ollama model**: set the env vars to any model tag Ollama can pull.
2. **Any OpenAI-compatible endpoint**: point `OPENAI_API_BASE` at vLLM,
   LM Studio, TGI, or another gateway serving an open model, and select the
   `openai` provider. This is how you run models larger than Ollama
   comfortably serves.

When evaluating a new vision model, test it on your own worst artifacts: a
dense table screenshot, a multi-series chart, a scanned handout. Alt-text
quality on clean photos is not the hard part; chart and document
understanding is.

## Honest quality expectations

- Local models produce noticeably plainer alt text and explanations than
  cloud flagships. For many institutions that trade is correct: the prose is
  reviewable in the remediation UI anyway, and the documents never leave.
- Scoring, scanning, severity, and verification are identical in local and
  cloud configurations — they never touch a model.
- You can mix: local vision for sensitive scanned documents, a cloud key for
  public web scans. Providers are configured per deployment, and BYOK
  per-department key overrides exist for cost attribution.
