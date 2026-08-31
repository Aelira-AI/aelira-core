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

## Evaluated defaults

AI remains disabled until an operator explicitly selects a provider. The model
tags below are used only after `LLM_PROVIDER=ollama` is configured; they do not
make Ollama, or any model, an implicit provider default.

The exact tags passed the repository's bounded local-model fixtures on
30 August 2026:

| Lane | Exact model | Evidence in this release |
|---|---|---|
| Vision | `qwen2.5vl:3b` | Recovered every value from the tracked chart and the required fields from a rasterized syllabus page |
| Text | `gemma3:4b` | Identified a tracked missing-alt issue, recommended the attribute fix, and preserved mandatory human review |
| Code | `qwen2.5-coder:7b` | Returned parseable HTML with two explicit input-label relationships and the submit control preserved |
| Embeddings | `nomic-embed-text:latest` | Ranked identical and alt-text-related WCAG text above an unrelated focus-visible control |

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

<!-- local-model-evidence:start -->
## Reproduced support matrix

The checked-in [machine-readable result](local-ai-model-results.json) was produced by evaluator `1.0.0` on Apple M3 Pro with 18.0 GiB memory. These measurements describe that host only; they are not universal hardware claims.

| Lane | Exact model | Status | Fixture cases | Median latency | Maximum latency | Download / loaded | Processor |
|---|---|---|---|---|---|---|---|
| Vision | `qwen2.5vl:3b` | **Supported** | quarterly-revenue-chart, scanned-syllabus-page | 1.77-1.83 s | 12.04-12.93 s | 3.0 GiB / 5.0 GiB | 0.0% CPU / 100.0% GPU |
| Text | `gemma3:4b` | **Supported** | missing-alt-explanation | 4.26 s | 6.11 s | 3.1 GiB / 5.0 GiB | 0.0% CPU / 100.0% GPU |
| Code | `qwen2.5-coder:7b` | **Supported** | form-label-repair | 7.65 s | 22.89 s | 4.4 GiB / 5.2 GiB | 0.0% CPU / 100.0% GPU |
| Embeddings | `nomic-embed-text:latest` | **Supported** | wcag-retrieval-ranking | 0.03 s | 0.59 s | 261.6 MiB / 809.4 MiB | 0.0% CPU / 100.0% GPU |

Supported means the exact tag and model ID passed every required fixture run for that lane. Other Ollama tags remain configurable and API-compatible, but unverified by this release. Missing, timed-out, malformed, or validator-failing runs remain unsupported rather than falling back to a claim.
<!-- local-model-evidence:end -->

## Hardware evidence

The checked-in run is evidence for one configuration: an 18 GB Apple M3 Pro,
Ollama 0.12.10, and sequential lane evaluation through Metal. The four model
downloads total about 10.7 GiB. Observed loaded size ranged from 809.4 MiB to
5.2 GiB because the evaluator runs one lane at a time.

No CPU-only host, 8 GB machine, discrete GPU, concurrent worker load, or
department-scale batch was measured. Run the same harness on the intended
host before treating it as supported:

```bash
python scripts/evaluate_local_models.py --output local-model-results.json
```

## Configuring other model tags

An operator may set the four `OLLAMA_*_MODEL` variables to other tags served
by Ollama. Those tags are API-compatible configuration choices, not supported
recommendations from this release. To promote another tag, add it to the
fixture matrix, run every required case repeatedly, and check in the resulting
evidence and generated documentation block.

Larger open models can also be served through an OpenAI-compatible endpoint by
selecting the `openai` provider and configuring `OPENAI_API_BASE`. That path is
outside this Ollama evidence matrix.

## Quality boundary

- The vision evidence covers one clean four-bar chart and one clean rasterized
  syllabus page. It does not establish support for dense tables, handwriting,
  damaged scans, arbitrary diagrams, or every OCR layout.
- The code evidence covers explicit labels for two form inputs. It does not
  establish general HTML, ARIA, table, or application-level repair quality.
- The text evidence proves a missing-alt explanation that refuses invented alt
  text and keeps human review mandatory. It does not compare tone or quality
  with cloud models.
- Embedding evidence proves one bounded WCAG retrieval ranking, not every
  corpus or language.
- Scoring, scanning, severity, and verification remain deterministic and work
  with no AI provider configured.

To validate the checked-in report and guide without running Ollama:

```bash
python scripts/evaluate_local_models.py --check
```
