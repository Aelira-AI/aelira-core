from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.ai.providers.base import LLMResponse
from src.ai.providers.types import ProviderType
from src.ai.workspace_provider_runtime import (
    WorkspaceProviderRuntime,
    WorkspaceProviderSnapshot,
)

PROVIDERS = tuple(provider.value for provider in ProviderType)


def _row(provider: str, *, credential: str | None = None, **models):
    return SimpleNamespace(
        provider=provider,
        api_key_encrypted=(credential if provider != "ollama" else None),
        text_model=models.get("text_model"),
        code_model=models.get("code_model"),
        vision_model=models.get("vision_model"),
    )


def _snapshot(
    workspace_id: str = "workspace-a",
    *,
    primary: str | None = "openai",
    fallback: str | None = None,
    rows=None,
):
    if rows is None:
        rows = [_row("openai", credential="cipher-openai")]
    return WorkspaceProviderSnapshot(
        workspace_id=workspace_id,
        primary=primary,
        fallback=fallback,
        rows={row.provider: row for row in rows},
    )


class _Provider:
    def __init__(self, name, config, events, *, succeeds=True):
        self.name = name
        self.config = config
        self.events = events
        self.succeeds = succeeds

    async def initialize(self):
        self.events.append(("initialize", self.name))
        return True

    async def close(self):
        self.events.append(("close", self.name))

    def _response(self, operation):
        self.events.append((operation, self.name))
        if not self.succeeds:
            return LLMResponse.error_response(
                error="attempt_failed",
                provider=self.name,
                model=getattr(self.config, f"{operation}_model", "") or "",
            )
        model_field = {
            "generate_text": "text_model",
            "generate_code": "code_model",
            "analyze_image": "vision_model",
            "generate_embedding": "embedding_model",
        }[operation]
        return LLMResponse.success_response(
            content=f"{self.name}:{operation}",
            provider=self.name,
            model=getattr(self.config, model_field) or "",
            inference_time=0.01,
        )

    async def generate_text(self, **_kwargs):
        return self._response("generate_text")

    async def generate_code(self, **_kwargs):
        return self._response("generate_code")

    async def analyze_image(self, **_kwargs):
        return self._response("analyze_image")

    async def generate_embedding(self, **_kwargs):
        return self._response("generate_embedding")


def _runtime(snapshot_loader, *, outcomes=None, events=None, decryptions=None):
    outcomes = outcomes or {}
    events = events if events is not None else []
    decryptions = decryptions if decryptions is not None else []

    def decrypt(ciphertext):
        decryptions.append(ciphertext)
        return f"plain:{ciphertext}"

    def factory(provider_type, config):
        events.append(
            (
                "construct",
                provider_type.value,
                config.api_key,
                config.text_model,
                config.code_model,
                config.vision_model,
            )
        )
        return _Provider(
            provider_type.value,
            config,
            events,
            succeeds=outcomes.get(provider_type.value, True),
        )

    return WorkspaceProviderRuntime(
        "workspace-a",
        snapshot_loader=snapshot_loader,
        decryptor=decrypt,
        provider_factory=factory,
    )


@pytest.mark.asyncio
async def test_close_cancellation_clears_plaintext_and_propagates():
    configs = []

    class _CancelledCloseProvider(_Provider):
        async def close(self):
            raise asyncio.CancelledError

    def factory(provider_type, config):
        configs.append(config)
        return _CancelledCloseProvider(provider_type.value, config, [])

    runtime = WorkspaceProviderRuntime(
        "workspace-a",
        snapshot_loader=lambda _workspace_id: _snapshot(),
        decryptor=lambda _ciphertext: "bounded-plaintext",
        provider_factory=factory,
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.generate_text(prompt="bounded prompt")

    assert len(configs) == 1
    assert configs[0].api_key is None


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_id", [None, "", "unknown"])
async def test_identity_missing_or_unknown_refuses_without_provider(workspace_id):
    events = []
    runtime = WorkspaceProviderRuntime(
        workspace_id,
        snapshot_loader=lambda _workspace_id: None,
        decryptor=lambda _value: pytest.fail("credential must not decrypt"),
        provider_factory=lambda *_args: events.append("constructed"),
    )

    response = await runtime.generate_text(prompt="explain")

    assert response.success is False
    assert response.error == "workspace_provider_unavailable"
    assert response.provider == "none"
    assert events == []


@pytest.mark.asyncio
async def test_fresh_resolution_observes_rotation_and_deletion():
    state = _snapshot()
    loads = []
    decryptions = []

    def load(workspace_id):
        loads.append(workspace_id)
        return state

    runtime = _runtime(load, decryptions=decryptions)
    first = await runtime.generate_text(prompt="one")
    state = replace(
        state,
        rows={"openai": _row("openai", credential="cipher-rotated")},
    )
    second = await runtime.generate_text(prompt="two")
    state = replace(state, rows={})
    third = await runtime.generate_text(prompt="three")

    assert first.success is True
    assert second.success is True
    assert third.success is False
    assert third.error == "workspace_provider_unavailable"
    assert loads == ["workspace-a", "workspace-a", "workspace-a"]
    assert decryptions == ["cipher-openai", "cipher-rotated"]


@pytest.mark.asyncio
async def test_neutral_null_constructs_and_decrypts_nothing():
    events = []
    decryptions = []
    runtime = _runtime(
        lambda _workspace_id: _snapshot(primary=None, rows=[]),
        events=events,
        decryptions=decryptions,
    )

    response = await runtime.generate_code(prompt="fix")

    assert response.success is False
    assert response.error == "workspace_provider_not_selected"
    assert events == []
    assert decryptions == []


@pytest.mark.asyncio
async def test_decrypts_only_attempted_selected_credential_and_closes_lifecycle():
    events = []
    decryptions = []
    runtime = _runtime(
        lambda _workspace_id: _snapshot(
            rows=[
                _row("openai", credential="cipher-primary"),
                _row("anthropic", credential="cipher-fallback"),
                _row("gemini", credential="cipher-unselected"),
            ],
            fallback="anthropic",
        ),
        events=events,
        decryptions=decryptions,
    )

    response = await runtime.generate_text(prompt="explain")

    assert response.success is True
    assert response.provider == "openai"
    assert decryptions == ["cipher-primary"]
    assert ("close", "openai") in events
    assert not any(event[1] == "anthropic" for event in events if len(event) > 1)


@pytest.mark.asyncio
async def test_fallback_is_explicit_and_same_workspace_only():
    events = []
    decryptions = []
    runtime = _runtime(
        lambda workspace_id: _snapshot(
            workspace_id,
            rows=[
                _row("openai", credential="cipher-primary"),
                _row("anthropic", credential="cipher-fallback"),
            ],
            fallback="anthropic",
        ),
        outcomes={"openai": False},
        events=events,
        decryptions=decryptions,
    )

    response = await runtime.generate_code(prompt="fix")

    assert response.success is True
    assert response.provider == "anthropic"
    assert decryptions == ["cipher-primary", "cipher-fallback"]
    assert [(event[0], event[1]) for event in events if event[0] == "close"] == [
        ("close", "openai"),
        ("close", "anthropic"),
    ]
    assert response.metadata["attempted_providers"] == ["openai", "anthropic"]


@pytest.mark.asyncio
async def test_missing_explicit_fallback_row_never_reaches_another_workspace():
    decryptions = []
    runtime = _runtime(
        lambda _workspace_id: _snapshot(
            rows=[_row("openai", credential="cipher-primary")],
            fallback="anthropic",
        ),
        outcomes={"openai": False},
        decryptions=decryptions,
    )

    response = await runtime.generate_text(prompt="explain")

    assert response.success is False
    assert response.error == "workspace_provider_attempts_failed"
    assert decryptions == ["cipher-primary"]
    assert response.metadata["attempted_providers"] == ["openai"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_supported_provider_and_model_overrides(provider):
    credential = None if provider == "ollama" else f"cipher-{provider}"
    runtime = _runtime(
        lambda workspace_id: _snapshot(
            workspace_id,
            primary=provider,
            rows=[
                _row(
                    provider,
                    credential=credential,
                    text_model=f"{provider}-text",
                    code_model=f"{provider}-code",
                    vision_model=f"{provider}-vision",
                )
            ],
        )
    )

    text = await runtime.generate_text(prompt="text")
    code = await runtime.generate_code(prompt="code")
    vision = await runtime.analyze_image(image_data=b"image", prompt="vision")

    assert (text.provider, text.model) == (provider, f"{provider}-text")
    assert (code.provider, code.model) == (provider, f"{provider}-code")
    assert (vision.provider, vision.model) == (provider, f"{provider}-vision")


def test_sync_consumers_keep_legacy_response_shapes():
    runtime = _runtime(lambda _workspace_id: _snapshot())

    text = runtime.generate_text_sync(prompt="text")
    code = runtime.generate_code_sync(prompt="code")
    vision = runtime.analyze_image_sync(image_data=b"image", prompt="vision")

    for response in (text, code, vision):
        assert response["success"] is True
        assert set(response) >= {
            "success",
            "content",
            "inference_time",
            "provider",
            "model",
        }


@pytest.mark.asyncio
async def test_two_tenant_isolation_never_loads_or_decrypts_other_workspace():
    snapshots = {
        "workspace-a": _snapshot(
            "workspace-a",
            primary="openai",
            rows=[_row("openai", credential="cipher-a")],
        ),
        "workspace-b": _snapshot(
            "workspace-b",
            primary="anthropic",
            rows=[_row("anthropic", credential="cipher-b")],
        ),
    }
    decryptions = []
    runtime = _runtime(
        lambda workspace_id: snapshots.get(workspace_id), decryptions=decryptions
    )

    response = await runtime.generate_text(prompt="tenant-a")

    assert response.provider == "openai"
    assert decryptions == ["cipher-a"]


@pytest.mark.asyncio
async def test_identical_prompts_do_not_share_cached_content_across_workspaces():
    snapshots = {
        "workspace-a": _snapshot(
            "workspace-a",
            primary="openai",
            rows=[_row("openai", credential="cipher-a")],
        ),
        "workspace-b": _snapshot(
            "workspace-b",
            primary="anthropic",
            rows=[_row("anthropic", credential="cipher-b")],
        ),
    }
    loads = []
    decryptions = []
    events = []

    def load(workspace_id):
        loads.append(workspace_id)
        return snapshots[workspace_id]

    def decrypt(ciphertext):
        decryptions.append(ciphertext)
        return f"plain:{ciphertext}"

    def factory(provider_type, config):
        return _Provider(provider_type.value, config, events)

    runtime_a = WorkspaceProviderRuntime(
        "workspace-a",
        snapshot_loader=load,
        decryptor=decrypt,
        provider_factory=factory,
    )
    runtime_b = WorkspaceProviderRuntime(
        "workspace-b",
        snapshot_loader=load,
        decryptor=decrypt,
        provider_factory=factory,
    )

    first = await runtime_a.generate_text(prompt="same prompt", use_cache=True)
    second = await runtime_b.generate_text(prompt="same prompt", use_cache=True)

    assert first.content == "openai:generate_text"
    assert second.content == "anthropic:generate_text"
    assert loads == ["workspace-a", "workspace-b"]
    assert decryptions == ["cipher-a", "cipher-b"]


@pytest.mark.asyncio
async def test_global_state_is_never_consulted(monkeypatch):
    monkeypatch.setattr(
        "src.ai.providers.get_provider_manager",
        lambda: pytest.fail("tenant runtime must not use global provider state"),
    )
    runtime = _runtime(lambda _workspace_id: _snapshot())

    response = await runtime.generate_text(prompt="isolated")

    assert response.success is True
