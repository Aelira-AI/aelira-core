import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  LLM_PROVIDER_CONTRACT_ERROR,
  SUPPORTED_LLM_PROVIDERS,
  normalizeProviderListResponse,
  normalizeProviderSelectionResponse,
  normalizeProviderTestResponse,
} from '../../src/utils/llmProviderContract.ts';

const provider = (name, overrides = {}) => ({
  name,
  display_name: name === 'xai' ? 'xAI' : name[0].toUpperCase() + name.slice(1),
  is_available: false,
  is_local: name === 'ollama',
  status: 'not_configured',
  text_model: null,
  code_model: null,
  vision_model: null,
  ...overrides,
});

const wireList = (overrides = {}) => ({
  primary: 'anthropic',
  fallback: 'ollama',
  providers: Object.fromEntries(
    SUPPORTED_LLM_PROVIDERS.map(name => [name, provider(name)]),
  ),
  ...overrides,
});

test('normalizes the keyed provider response in stable supported-provider order', () => {
  const response = normalizeProviderListResponse(wireList({
    providers: {
      ...wireList().providers,
      ollama: provider('ollama', {
        display_name: 'Ollama',
        is_available: true,
        status: 'healthy',
        text_model: 'qwen2.5:7b',
      }),
      future_provider: provider('future_provider'),
    },
  }));

  assert.deepEqual(response.providers.map(item => item.name), [
    'ollama',
    'gemini',
    'openai',
    'anthropic',
    'xai',
  ]);
  assert.equal(response.primary_provider, 'anthropic');
  assert.equal(response.fallback_provider, 'ollama');
  assert.deepEqual(response.providers[0], {
    name: 'ollama',
    display_name: 'Ollama',
    is_available: true,
    is_local: true,
    status: 'healthy',
    text_model: 'qwen2.5:7b',
    code_model: null,
    vision_model: null,
  });
});

test('preserves provider-neutral null selections', () => {
  const response = normalizeProviderListResponse(wireList({
    primary: null,
    fallback: null,
  }));

  assert.equal(response.primary_provider, null);
  assert.equal(response.fallback_provider, null);
});

test('rejects malformed successful provider responses with one bounded error', () => {
  for (const response of [
    null,
    { primary: null, fallback: null, providers: [] },
    wireList({ primary: 'unsupported' }),
    wireList({ providers: { ...wireList().providers, ollama: provider('gemini') } }),
    wireList({ providers: { ...wireList().providers, gemini: { ...provider('gemini'), is_available: 'yes' } } }),
  ]) {
    assert.throws(
      () => normalizeProviderListResponse(response),
      error => error instanceof Error && error.message === LLM_PROVIDER_CONTRACT_ERROR,
    );
  }
});

test('normalizes primary-selection and provider-test responses from server field names', () => {
  assert.deepEqual(
    normalizeProviderSelectionResponse({
      success: true,
      message: 'Set openai as primary provider',
      primary: 'openai',
      fallback: 'ollama',
    }),
    {
      success: true,
      message: 'Set openai as primary provider',
      primary_provider: 'openai',
      fallback_provider: 'ollama',
    },
  );

  assert.deepEqual(
    normalizeProviderTestResponse({
      success: true,
      provider: 'gemini',
      model: 'gemini-3-flash',
      inference_time: 1.234,
      response_preview: 'WCAG is an accessibility standard.',
      error: null,
    }),
    {
      success: true,
      provider: 'gemini',
      model: 'gemini-3-flash',
      response_time_ms: 1234,
      response_preview: 'WCAG is an accessibility standard.',
      error: null,
    },
  );
});

test('preserves bounded provider-test failures when no provider was attempted', () => {
  assert.deepEqual(
    normalizeProviderTestResponse({
      success: false,
      provider: 'none',
      model: '',
      inference_time: 0,
      response_preview: null,
      error: 'No providers available',
    }),
    {
      success: false,
      provider: 'none',
      model: '',
      response_time_ms: 0,
      response_preview: null,
      error: 'No providers available',
    },
  );
});

test('Settings consumes normalized arrays and server-returned provider selections', () => {
  const source = readFileSync(new URL('../../src/pages/Settings.tsx', import.meta.url), 'utf8');

  assert.match(source, /for \(const p of data\.providers\)/);
  assert.match(source, /const result = await llmProvidersApi\.setPrimaryProvider/);
  assert.match(source, /setPrimaryProvider\(result\.primary_provider\)/);
  assert.match(source, /setFallbackProvider\(result\.fallback_provider\)/);
  assert.doesNotMatch(source, /setPrimaryProvider\(providerKey\)/);
});
