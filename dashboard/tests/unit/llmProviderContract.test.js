import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  LLM_PROVIDER_CONTRACT_ERROR,
  SUPPORTED_LLM_PROVIDERS,
  normalizeProviderListResponse,
  normalizeProviderRevisionConflict,
  normalizeProviderSelectionResponse,
  normalizeProviderTestResponse,
} from '../../src/utils/llmProviderContract.ts';

const provider = (name, overrides = {}) => ({
  name,
  display_name: name === 'xai' ? 'xAI' : name[0].toUpperCase() + name.slice(1),
  configured: false,
  is_available: false,
  is_local: name === 'ollama',
  status: 'not_configured',
  text_model: null,
  code_model: null,
  vision_model: null,
  ...overrides,
});

const wireList = (overrides = {}) => ({
  schema_version: 1,
  config_revision: 4,
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
        configured: true,
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
  assert.equal(response.config_revision, 4);
  assert.deepEqual(response.providers[0], {
    name: 'ollama',
    display_name: 'Ollama',
    configured: true,
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
      config_revision: 5,
    }),
    {
      success: true,
      message: 'Set openai as primary provider',
      primary_provider: 'openai',
      fallback_provider: 'ollama',
      config_revision: 5,
    },
  );

  assert.deepEqual(
    normalizeProviderTestResponse({
      success: true,
      provider: 'gemini',
      model: 'gemini-3-flash',
      inference_time: 1.234,
      error: null,
    }),
    {
      success: true,
      provider: 'gemini',
      model: 'gemini-3-flash',
      response_time_ms: 1234,
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
      error: 'No providers available',
    }),
    {
      success: false,
      provider: 'none',
      model: '',
      response_time_ms: 0,
      error: 'No providers available',
    },
  );
});

test('recovers only a validated server-authored provider revision conflict', () => {
  const current = wireList({ config_revision: 9, primary: 'openai', fallback: null });
  const recovered = normalizeProviderRevisionConflict({
    response: {
      data: {
        detail: {
          code: 'provider_config_revision_conflict',
          reason: 'stale_revision',
          current,
        },
      },
    },
  });

  assert.equal(recovered?.config_revision, 9);
  assert.equal(recovered?.primary_provider, 'openai');
  assert.equal(normalizeProviderRevisionConflict({ response: { data: { detail: 'no' } } }), null);
  assert.equal(normalizeProviderRevisionConflict({
    response: { data: { detail: { code: 'provider_config_revision_conflict', current: {} } } },
  }), null);
});

test('Settings consumes durable workspace state and gates provider controls by role', () => {
  const source = readFileSync(new URL('../../src/pages/Settings.tsx', import.meta.url), 'utf8');
  const card = readFileSync(new URL('../../src/components/settings/AIProvidersCard.tsx', import.meta.url), 'utf8');

  assert.match(source, /for \(const p of data\.providers\)/);
  assert.match(source, /const result = await llmProvidersApi\.updateSelection/);
  assert.match(source, /user\?\.role === 'admin'/);
  assert.match(source, /authMethod !== 'lti'/);
  assert.match(source, /useState<number \| null>\(null\)/);
  assert.match(source, /setProviderLoadError\(true\)/);
  assert.match(card, /No changes can be made until current/);
  assert.match(card, /providerLoadError \|\| !providerStateReady/);
  assert.match(source, /normalizeProviderRevisionConflict\(error\)/);
  assert.match(source, /provider-\$\{providerKey\}-row/);
  assert.match(source, /setProviderMutationPending\(true\)/);
  assert.match(card, /disabled=\{providerMutationPending\}/);
  assert.doesNotMatch(source, /Failed to configure provider:', error/);
  assert.match(source, /setPrimaryProvider\(result\.primary_provider\)/);
  assert.match(source, /setFallbackProvider\(result\.fallback_provider\)/);
  assert.doesNotMatch(source, /setPrimaryProvider\(providerKey\)/);
  assert.doesNotMatch(card, /Gemini 3\s+by default/);
  assert.doesNotMatch(card, /higher\s+department tier/);
  assert.match(card, /Contact them to confirm whether AI is/);
  assert.doesNotMatch(card, /Alt text generation/);
  assert.match(card, /aria-busy=\{testingProvider === key\}/);
  assert.match(card, /aria-busy=\{configuringProvider === key\}/);
  assert.match(card, /Set Fallback/);
  assert.match(card, /Clear Fallback/);
});

test('provider controls stay inside their cards at narrow widths', () => {
  const card = readFileSync(new URL('../../src/components/settings/AIProvidersCard.tsx', import.meta.url), 'utf8');

  assert.match(card, /flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between/);
  assert.match(card, /flex min-w-0 items-start gap-3/);
  assert.match(card, /justify-start gap-2 sm:justify-end/);
});
