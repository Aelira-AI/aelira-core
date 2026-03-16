# LLM Provider Abstraction Layer

**Version:** 1.0.0
**Added:** December 2025
**Status:** Production Ready

The LLM Provider Abstraction Layer enables flexible AI model selection, allowing users to choose between local (Ollama) and cloud (Gemini, OpenAI, Anthropic) providers based on their preferences, hardware, and privacy requirements.

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Architecture](#architecture)
4. [Providers](#providers)
5. [Configuration](#configuration)
6. [API Endpoints](#api-endpoints)
7. [Code Examples](#code-examples)
8. [Adding Custom Providers](#adding-custom-providers)

---

## Overview

### Why Multiple Providers?

- **Privacy**: Use Ollama for local processing - no data leaves your infrastructure
- **Cost**: Use your own API keys for OpenAI/Anthropic instead of shared infrastructure
- **Flexibility**: Switch providers at runtime without code changes
- **Reliability**: Automatic fallback when primary provider fails

### Supported Providers

| Provider | Type | Best For | Privacy |
|----------|------|----------|---------|
| **Gemini** | Cloud | Default, fast, free tier | Data sent to Google |
| **Ollama** | Local | Privacy, air-gapped deployments | 100% local |
| **OpenAI** | Cloud | GPT-4 capabilities | Data sent to OpenAI |
| **Anthropic** | Cloud | Claude models | Data sent to Anthropic |

### Capabilities Matrix

| Provider | Text | Code | Vision | Embeddings | Streaming |
|----------|:----:|:----:|:------:|:----------:|:---------:|
| Gemini | ✅ | ✅ | ✅ | ❌ | ✅ |
| Ollama | ✅ | ✅ | ✅ | ✅ | ❌ |
| OpenAI | ✅ | ✅ | ✅ | ✅ | ✅ |
| Anthropic | ✅ | ✅ | ✅ | ❌ | ✅ |

---

## Quick Start

### 1. Configure Environment Variables

```bash
# Copy the example file
cp .env.example .env

# Edit .env with your settings
```

### 2. Set Your Preferred Provider

```bash
# In .env file
LLM_PROVIDER=gemini          # Primary provider
LLM_FALLBACK_PROVIDER=ollama # Fallback when primary fails

# Add your API key for the chosen provider
GEMINI_API_KEY=your-key-here
```

### 3. Use the Provider Manager

```python
from src.ai.providers import get_provider_manager, ProviderType

async def main():
    manager = get_provider_manager()
    await manager.initialize()

    # Use default provider (with automatic fallback)
    response = await manager.generate_text("Explain WCAG 2.1")
    print(response.content)

    # Or specify a provider
    response = await manager.generate_text(
        "Explain WCAG 2.1",
        provider=ProviderType.OLLAMA
    )
```

---

## Architecture

### Module Structure

```
backend/src/ai/providers/
├── __init__.py           # Public exports
├── base.py               # LLMProvider abstract class, LLMResponse
├── types.py              # ProviderType enum, ProviderConfig
├── manager.py            # ProviderManager (selection, fallback)
├── gemini_provider.py    # Google Gemini implementation
├── ollama_provider.py    # Ollama local implementation
├── openai_provider.py    # OpenAI implementation
└── anthropic_provider.py # Anthropic Claude implementation
```

### Class Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     ProviderManager                          │
│  - Manages provider selection and fallback                   │
│  - Methods: generate_text(), generate_code(), analyze_image()│
└─────────────────────────────────────────────────────────────┘
                              │
                              │ uses
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  LLMProvider (Abstract)                      │
│  - name, display_name, capabilities, is_available            │
│  - initialize(), close(), health_check()                     │
│  - generate_text(), generate_code(), analyze_image()         │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  GeminiProvider │ │  OllamaProvider │ │  OpenAIProvider │
│  (Cloud)        │ │  (Local)        │ │  (Cloud)        │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

### Key Classes

#### LLMResponse

Standardized response from any provider:

```python
@dataclass
class LLMResponse:
    success: bool           # Whether the request succeeded
    content: str            # Generated content
    provider: str           # Provider name (e.g., 'gemini')
    model: str              # Model used (e.g., 'gemini-2.0-flash')
    inference_time: float   # Time in seconds
    error: Optional[str]    # Error message if failed
    metadata: Dict[str, Any] # Additional data (embeddings, etc.)

    # Token usage (if available)
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
```

#### ProviderCapability

Flags indicating what a provider supports:

```python
class ProviderCapability(Flag):
    TEXT_GENERATION = auto()   # Text completion
    CODE_GENERATION = auto()   # Code generation
    VISION = auto()            # Image analysis
    EMBEDDINGS = auto()        # Text embeddings
    STREAMING = auto()         # Streaming responses
    FUNCTION_CALLING = auto()  # Tool/function calling
```

---

## Providers

### Gemini (Default)

Google's Gemini models - fast, accurate, free tier available.

**Configuration:**
```bash
GEMINI_API_KEY=your-key-here
GEMINI_TEXT_MODEL=gemini-2.0-flash      # Default
GEMINI_CODE_MODEL=gemini-2.0-flash      # Default
GEMINI_VISION_MODEL=gemini-2.5-flash-image  # Default
```

**Get API Key:** https://makersuite.google.com/app/apikey

**Available Models:**
- `gemini-2.0-flash` - Fast, free tier (default)
- `gemini-2.5-flash-image` - Best for image analysis
- `gemini-2.5-pro` - Most accurate text model

### Ollama (Local)

Run AI locally - no data leaves your machine.

**Configuration:**
```bash
OLLAMA_HOST=http://localhost:11434
OLLAMA_TEXT_MODEL=llama3.2:3b           # Default
OLLAMA_CODE_MODEL=qwen2.5-coder:3b      # Default
OLLAMA_VISION_MODEL=llava:7b            # Default
OLLAMA_EMBEDDING_MODEL=nomic-embed-text # Default
```

**Install Ollama:** https://ollama.ai

**Pull Required Models:**
```bash
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:3b
ollama pull llava:7b
ollama pull nomic-embed-text
```

**Available Models:**
- `llama3.2:3b` - Fast general-purpose
- `qwen2.5-coder:3b` - Best for code (100% accuracy in benchmarks)
- `qwen2.5-coder:1.5b` - Lighter, for limited RAM
- `llava:7b` - Vision model
- `moondream:latest` - Fast vision (10x faster than llava)

### OpenAI

Use your own OpenAI API key for GPT models.

**Configuration:**
```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_TEXT_MODEL=gpt-4o-mini           # Default
OPENAI_CODE_MODEL=gpt-4o                # Default
OPENAI_VISION_MODEL=gpt-4o              # Default
```

**Get API Key:** https://platform.openai.com/api-keys

**Available Models:**
- `gpt-4o` - Most capable
- `gpt-4o-mini` - Fast and affordable
- `gpt-4-turbo` - Previous generation

### Anthropic

Use your own Anthropic API key for Claude models.

**Configuration:**
```bash
ANTHROPIC_API_KEY=sk-ant-your-key-here
ANTHROPIC_TEXT_MODEL=claude-3-5-sonnet-20241022  # Default
ANTHROPIC_CODE_MODEL=claude-3-5-sonnet-20241022  # Default
ANTHROPIC_VISION_MODEL=claude-3-5-sonnet-20241022 # Default
```

**Get API Key:** https://console.anthropic.com/account/keys

**Available Models:**
- `claude-3-5-sonnet-20241022` - Best balance (default)
- `claude-3-opus-20240229` - Most capable
- `claude-3-5-haiku-20241022` - Fastest

---

## Configuration

### Environment Variables

```bash
# ===========================================
# LLM Provider Configuration
# ===========================================

# Primary provider (default: gemini)
# Options: gemini, ollama, openai, anthropic
LLM_PROVIDER=gemini

# Fallback provider when primary fails (default: ollama)
LLM_FALLBACK_PROVIDER=ollama

# ===========================================
# Provider-Specific Settings
# ===========================================

# Gemini (Google)
GEMINI_API_KEY=your-key-here
GEMINI_TEXT_MODEL=gemini-2.0-flash
GEMINI_CODE_MODEL=gemini-2.0-flash
GEMINI_VISION_MODEL=gemini-2.5-flash-image

# Ollama (Local)
OLLAMA_HOST=http://localhost:11434
OLLAMA_TEXT_MODEL=llama3.2:3b
OLLAMA_CODE_MODEL=qwen2.5-coder:3b
OLLAMA_VISION_MODEL=llava:7b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# OpenAI (Optional - User's Own Key)
OPENAI_API_KEY=sk-your-key-here
OPENAI_TEXT_MODEL=gpt-4o-mini
OPENAI_CODE_MODEL=gpt-4o
OPENAI_VISION_MODEL=gpt-4o

# Anthropic (Optional - User's Own Key)
ANTHROPIC_API_KEY=sk-ant-your-key-here
ANTHROPIC_TEXT_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_CODE_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_VISION_MODEL=claude-3-5-sonnet-20241022
```

### Programmatic Configuration

```python
from src.ai.providers import ProviderManager, ProviderConfig, ProviderType

# Create custom configuration
config = ProviderConfig(
    provider_type=ProviderType.OPENAI,
    api_key="sk-your-key",
    text_model="gpt-4o",
    code_model="gpt-4o",
    vision_model="gpt-4o",
    timeout=120,
)

# Initialize with custom config
manager = ProviderManager(
    primary_provider=ProviderType.OPENAI,
    fallback_provider=ProviderType.OLLAMA,
    configs={ProviderType.OPENAI: config}
)

await manager.initialize()
```

---

## API Endpoints

The LLM Provider system exposes REST API endpoints for provider management.

### List Providers

**GET** `/llm/providers`

Returns all available providers and their status.

**Response:**
```json
{
  "primary": "gemini",
  "fallback": "ollama",
  "providers": {
    "gemini": {
      "name": "gemini",
      "display_name": "Google Gemini",
      "is_available": true,
      "is_local": false,
      "status": "healthy",
      "text_model": "gemini-2.0-flash",
      "code_model": "gemini-2.0-flash",
      "vision_model": "gemini-2.5-flash-image"
    },
    "ollama": {
      "name": "ollama",
      "display_name": "Ollama (Local)",
      "is_available": true,
      "is_local": true,
      "status": "healthy",
      "text_model": "llama3.2:3b",
      "code_model": "qwen2.5-coder:3b",
      "vision_model": "llava:7b"
    }
  }
}
```

### Set Primary Provider

**POST** `/llm/providers/primary`

Change the primary or fallback provider.

**Request:**
```json
{
  "provider": "ollama",
  "as_fallback": false
}
```

**Response:**
```json
{
  "success": true,
  "message": "Set ollama as primary provider",
  "primary": "ollama",
  "fallback": "gemini"
}
```

### Add Provider with API Key

**POST** `/llm/providers/add`

Configure a new provider with your API key.

**Request:**
```json
{
  "provider": "openai",
  "api_key": "sk-your-key-here",
  "text_model": "gpt-4o-mini",
  "code_model": "gpt-4o"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Provider openai configured successfully",
  "provider": "openai"
}
```

### Test Provider

**POST** `/llm/providers/test?provider=gemini`

Test a provider with a simple prompt.

**Response:**
```json
{
  "success": true,
  "provider": "gemini",
  "model": "gemini-2.0-flash",
  "inference_time": 1.234,
  "response_preview": "WCAG 2.1 is a set of guidelines..."
}
```

### List Provider Models

**GET** `/llm/providers/{provider}/models`

Get available models for a specific provider.

**Response:**
```json
{
  "provider": "ollama",
  "models": [
    "llama3.2:3b",
    "qwen2.5-coder:3b",
    "llava:7b"
  ]
}
```

### Health Check

**GET** `/llm/health`

Check health of all providers.

**Response:**
```json
{
  "status": "healthy",
  "primary_provider": "gemini",
  "fallback_provider": "ollama",
  "providers": {
    "gemini": {"status": "healthy", ...},
    "ollama": {"status": "healthy", ...}
  }
}
```

---

## Code Examples

### Basic Text Generation

```python
from src.ai.providers import get_provider_manager

async def generate_explanation():
    manager = get_provider_manager()
    await manager.initialize()

    response = await manager.generate_text(
        prompt="Explain WCAG 2.1 Level AA requirements",
        max_tokens=500,
        temperature=0.3,
    )

    if response.success:
        print(f"Response: {response.content}")
        print(f"Provider: {response.provider}")
        print(f"Time: {response.inference_time:.2f}s")
    else:
        print(f"Error: {response.error}")
```

### Code Generation

```python
async def generate_accessibility_fix():
    manager = get_provider_manager()
    await manager.initialize()

    response = await manager.generate_code(
        prompt="""
        Fix this HTML for accessibility:
        <img src="chart.png">

        Add proper alt text and ARIA attributes.
        """,
        language="html",
        max_tokens=1000,
        temperature=0.2,
    )

    print(response.content)
```

### Using a Specific Provider

```python
from src.ai.providers import get_provider_manager, ProviderType

async def use_local_provider():
    manager = get_provider_manager()
    await manager.initialize()

    # Force use of Ollama (local)
    response = await manager.generate_text(
        prompt="Analyze this accessibility issue...",
        provider=ProviderType.OLLAMA,
    )

    print(f"Processed locally: {response.content}")
```

### Image Analysis

```python
async def analyze_image():
    manager = get_provider_manager()
    await manager.initialize()

    with open("chart.png", "rb") as f:
        image_data = f.read()

    response = await manager.analyze_image(
        image_data=image_data,
        prompt="Describe this image for a blind user",
        max_tokens=200,
    )

    print(f"Alt text: {response.content}")
```

### Switching Providers at Runtime

```python
async def switch_providers():
    manager = get_provider_manager()
    await manager.initialize()

    # Start with Gemini
    print(f"Primary: {manager.primary_type.value}")

    # Switch to OpenAI
    success = manager.set_primary_provider(ProviderType.OPENAI)
    if success:
        print(f"Switched to: {manager.primary_type.value}")

    # Disable fallback
    manager.set_fallback_provider(None)
```

### Adding a Provider Dynamically

```python
from src.ai.providers import get_provider_manager, ProviderConfig, ProviderType

async def add_openai_provider():
    manager = get_provider_manager()
    await manager.initialize()

    # Add OpenAI with user's API key
    config = ProviderConfig.default_for_provider(ProviderType.OPENAI)
    config.api_key = "sk-user-provided-key"

    success = await manager.add_provider(ProviderType.OPENAI, config)

    if success:
        # Make it the primary
        manager.set_primary_provider(ProviderType.OPENAI)
```

---

## Adding Custom Providers

To add a new provider, implement the `LLMProvider` abstract class:

```python
from src.ai.providers.base import LLMProvider, LLMResponse, ProviderCapability

class CustomProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "custom"

    @property
    def display_name(self) -> str:
        return "My Custom Provider"

    @property
    def capabilities(self) -> ProviderCapability:
        return (
            ProviderCapability.TEXT_GENERATION
            | ProviderCapability.CODE_GENERATION
        )

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def initialize(self) -> bool:
        # Setup connections, verify keys
        return True

    async def close(self) -> None:
        # Cleanup resources
        pass

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        # Implement text generation
        pass

    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
    ) -> LLMResponse:
        # Implement code generation
        pass

    def health_check(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "provider": self.name,
        }
```

Then register it in `manager.py`:

```python
def _create_provider(self, provider_type: ProviderType) -> LLMProvider:
    if provider_type == ProviderType.CUSTOM:
        return CustomProvider(config)
    # ... existing providers
```

---

## Troubleshooting

### Provider Not Available

```
Error: Provider openai is not available
```

**Solution:** Configure the provider with an API key:
```bash
OPENAI_API_KEY=sk-your-key-here
```

Or add it via API:
```bash
curl -X POST http://localhost:8000/llm/providers/add \
  -H "Content-Type: application/json" \
  -d '{"provider": "openai", "api_key": "sk-your-key"}'
```

### Ollama Connection Failed

```
Error: Ollama connection refused
```

**Solution:** Ensure Ollama is running:
```bash
ollama serve
```

### Model Not Found

```
Error: Model llama3.2:3b not available
```

**Solution:** Pull the required model:
```bash
ollama pull llama3.2:3b
```

### Fallback Triggered

Check logs for:
```
WARNING: Gemini failed, trying Ollama: API error 429
```

This is normal - the system automatically falls back when primary fails.

---

## Best Practices

1. **Always set a fallback provider** for reliability
2. **Use Ollama for sensitive data** - keeps everything local
3. **Use Gemini's free tier** for development
4. **Let users provide their own API keys** for cloud providers
5. **Monitor provider health** via `/llm/health` endpoint
6. **Test providers** before switching with `/llm/providers/test`

---

**Made with 💜 by the Aelira team**

*Last Updated: December 2025*
