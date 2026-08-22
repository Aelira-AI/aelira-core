"""Secret-free LMS AI provider readiness and credential rules.

Readiness deliberately returns a small bounded vocabulary.  It never returns
keys, ciphertext, model lists, or configured hosts.  Execution still resolves
credentials per call; a readiness result is not an authorization grant.
"""

from __future__ import annotations

import os
import ipaddress
import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlsplit

from src.ai.providers.types import ProviderConfig, ProviderType

PROVIDERS = ("ollama", "gemini", "openai", "anthropic", "xai")
ReadinessReason = Literal[
    "ready",
    "credentials_forbidden",
    "ambient_key_forbidden",
    "host_not_loopback",
    "unreachable",
    "model_missing",
    "credential_provider_mismatch",
    "credentials_missing",
    "credential_invalid",
    "pilot_not_approved",
    "platform_key_missing",
]


@dataclass(frozen=True)
class ProviderReadiness:
    ready: bool
    reason: ReadinessReason
    locality: Literal["local", "remote"]
    credential_source: Literal["local", "department_byok", "platform"] | None = None


def production_decrypt_api_key(value: str) -> str:
    """Use the production BYOK decryptor without importing encryption eagerly."""
    from src.utils.encryption import decrypt_api_key

    return decrypt_api_key(value)


def canonical_loopback_ollama_host(host: object) -> str | None:
    """Canonicalize an explicit HTTP(S) loopback-only Ollama endpoint."""
    if not isinstance(host, str) or not host:
        return None
    try:
        parsed = urlsplit(host)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.hostname is None
        ):
            return None
        hostname = parsed.hostname.lower()
        address = (
            ipaddress.ip_address("127.0.0.1")
            if hostname == "localhost"
            else ipaddress.ip_address(hostname)
        )
        if not address.is_loopback:
            return None
        literal = "[::1]" if address.version == 6 else "127.0.0.1"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{literal}{port}"
    except (ValueError, TypeError):
        return None


def _ollama_models(config: ProviderConfig) -> set[str] | None:
    """Probe only the canonical loopback host with a finite timeout."""
    try:
        import ollama

        client = ollama.Client(
            host=config.host,
            trust_env=False,
            follow_redirects=False,
            timeout=2,
        )
        response = client.list()
        close = getattr(client, "close", None)
        if callable(close):
            close()
        raw = (
            response.models
            if hasattr(response, "models")
            else response.get("models", [])
        )
        return {
            str(getattr(item, "model", None) or item.get("name"))
            for item in raw
            if getattr(item, "model", None)
            or (isinstance(item, dict) and item.get("name"))
        }
    except Exception:
        return None


def _bounded_ollama_runtime_timeout(value: object) -> int:
    """Preserve a valid inference timeout while enforcing the provider bounds."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        return 120
    return int(max(1, min(120, value)))


def resolve_lms_provider_config(
    department: Any,
    provider: str,
    *,
    environment: Mapping[str, str] | None = None,
    decrypt_api_key: Callable[[str], str] = production_decrypt_api_key,
    materialize_credentials: bool = True,
    probe_ollama: bool = False,
    ollama_probe: Callable[[ProviderConfig], set[str] | None] | None = None,
) -> tuple[ProviderConfig | None, ProviderReadiness]:
    """Apply the same exact credential/locality rules used at execution time."""
    env = environment if environment is not None else os.environ
    provider_type = ProviderType(provider)
    config = ProviderConfig.default_for_provider(provider_type)

    if provider_type is ProviderType.OLLAMA:
        if getattr(department, "byok_provider", None) is not None or getattr(
            department, "byok_api_key_encrypted", None
        ):
            return None, ProviderReadiness(False, "credentials_forbidden", "local")
        if env.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_API_KEY"):
            return None, ProviderReadiness(False, "ambient_key_forbidden", "local")
        host = env.get("OLLAMA_HOST", config.host or "")
        canonical = canonical_loopback_ollama_host(host)
        if canonical is None:
            return None, ProviderReadiness(False, "host_not_loopback", "local")
        config.host = canonical
        config.api_key = None
        config.timeout = _bounded_ollama_runtime_timeout(config.timeout)
        if probe_ollama:
            models = (ollama_probe or _ollama_models)(replace(config))
            if models is None:
                return None, ProviderReadiness(False, "unreachable", "local")
            required = {config.text_model, config.code_model, config.vision_model}
            if not required.issubset(models):
                return None, ProviderReadiness(False, "model_missing", "local")
        return config, ProviderReadiness(True, "ready", "local", "local")

    byok_provider = getattr(department, "byok_provider", None)
    encrypted = getattr(department, "byok_api_key_encrypted", None)
    if byok_provider is not None and byok_provider != provider:
        return None, ProviderReadiness(False, "credential_provider_mismatch", "remote")
    if byok_provider == provider:
        if not encrypted:
            return None, ProviderReadiness(False, "credentials_missing", "remote")
        try:
            key = decrypt_api_key(encrypted)
        except Exception:
            return None, ProviderReadiness(False, "credential_invalid", "remote")
        if not isinstance(key, str) or not key:
            return None, ProviderReadiness(False, "credential_invalid", "remote")
        if materialize_credentials:
            config.api_key = key
        del key
        return config, ProviderReadiness(True, "ready", "remote", "department_byok")
    if encrypted:
        return None, ProviderReadiness(False, "credential_provider_mismatch", "remote")

    if provider_type is ProviderType.GEMINI:
        if getattr(department, "pilot_gemini_approved", None) is not True:
            return None, ProviderReadiness(False, "pilot_not_approved", "remote")
        if not env.get("GEMINI_API_KEY"):
            return None, ProviderReadiness(False, "platform_key_missing", "remote")
        if materialize_credentials:
            config.api_key = env["GEMINI_API_KEY"]
        return config, ProviderReadiness(True, "ready", "remote", "platform")
    return None, ProviderReadiness(False, "credentials_missing", "remote")


def resolve_lms_ai_readiness(
    department: Any,
    *,
    environment: Mapping[str, str] | None = None,
    decrypt_api_key: Callable[[str], str] = production_decrypt_api_key,
    ollama_probe: Callable[[ProviderConfig], set[str] | None] | None = None,
) -> dict[str, ProviderReadiness]:
    """Resolve all provider options, validating but never retaining cloud secrets."""
    return {
        provider: resolve_lms_provider_config(
            department,
            provider,
            environment=environment,
            decrypt_api_key=decrypt_api_key,
            materialize_credentials=False,
            probe_ollama=provider == "ollama",
            ollama_probe=ollama_probe,
        )[1]
        for provider in PROVIDERS
    }
