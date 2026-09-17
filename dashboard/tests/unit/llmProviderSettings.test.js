import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import {
  canSaveOllama,
  isWorkspaceAIDisabled,
  modelDraftFromProvider,
  modelIdentifierError,
  ollamaConfigurationOptions,
  ollamaTestFailure,
  providerConfigurationRequest,
  providerSelectionRequest,
} from '../../src/utils/llmProviderSettings.ts';
import { normalizeProviderListResponse } from '../../src/utils/llmProviderContract.ts';

const models = {
  textModel: 'gemma3:4b',
  codeModel: 'qwen2.5-coder:7b',
  visionModel: 'qwen2.5vl:3b',
};
const ollama = {
  configured: true,
  text_model: models.textModel,
  code_model: models.codeModel,
  vision_model: models.visionModel,
};

test('model validation matches server identifiers, including namespace, casing and length', () => {
  for (const value of ['a', 'Org/Model_1.2:Q4_K+M', 'x'.repeat(128), ...Object.values(models)]) {
    assert.equal(modelIdentifierError(value), null, value);
  }
  for (const value of ['', ' ', ' model', 'model ', 'model\n', 'model\r', 'a\nb', '/model', '-model', 'é', 'a?b', 'a@b', 'x'.repeat(129)]) {
    assert.equal(typeof modelIdentifierError(value), 'string', JSON.stringify(value));
    assert.throws(() => ollamaConfigurationOptions({ ...models, textModel: value }));
  }
});

test('Ollama save sends all exact identifiers and the current revision without a credential mutation', () => {
  const draft = { ...models, textModel: 'Org/Model_1.2:Q4_K+M' };
  const payload = providerConfigurationRequest(26, ollamaConfigurationOptions(draft));
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    expected_revision: 26,
    text_model: draft.textModel,
    code_model: draft.codeModel,
    vision_model: draft.visionModel,
  });
  assert.deepEqual(draft, { ...models, textModel: 'Org/Model_1.2:Q4_K+M' });
  for (const field of ['textModel', 'codeModel', 'visionModel']) {
    assert.throws(() => ollamaConfigurationOptions({ ...models, [field]: '' }));
  }
});

test('authoritative reload retains all saved model values; an unconfigured provider gets no invented defaults', () => {
  const payload = providerConfigurationRequest(26, models);
  const providerNames = ['ollama', 'gemini', 'openai', 'anthropic', 'xai'];
  const response = normalizeProviderListResponse({
    schema_version: 1, config_revision: 27, primary: null, fallback: null,
    providers: Object.fromEntries(providerNames.map(name => [name, {
      name, display_name: name, configured: name === 'ollama', is_available: name === 'ollama',
      is_local: name === 'ollama', status: name === 'ollama' ? 'configured' : 'not_configured',
      text_model: name === 'ollama' ? payload.text_model : null,
      code_model: name === 'ollama' ? payload.code_model : null,
      vision_model: name === 'ollama' ? payload.vision_model : null,
    }])),
  });
  assert.deepEqual(modelDraftFromProvider(response.providers[0]), models);
  assert.deepEqual(modelDraftFromProvider(undefined), { textModel: '', codeModel: '', visionModel: '' });
});

test('save locks while pending or any identifier is invalid', () => {
  assert.equal(canSaveOllama(models, false), true);
  assert.equal(canSaveOllama(models, true), false);
  for (const field of ['textModel', 'codeModel', 'visionModel']) {
    assert.equal(canSaveOllama({ ...models, [field]: '' }, false), false);
    assert.equal(canSaveOllama({ ...models, [field]: 'bad model' }, false), false);
  }
});

test('disable clears both selections while preserving an existing fallback-only selection', () => {
  assert.equal(isWorkspaceAIDisabled(null, null), true);
  for (const [primary, fallback] of [['ollama', null], [null, 'ollama'], ['openai', 'ollama']]) {
    assert.equal(isWorkspaceAIDisabled(primary, fallback), false);
    assert.deepEqual(providerSelectionRequest(26, primary, fallback), { expected_revision: 26, primary, fallback });
  }
  assert.deepEqual(providerSelectionRequest(26, null, null), { expected_revision: 26, primary: null, fallback: null });
});

test('Ollama failure guidance is bounded, text-only, and does not claim the cause', () => {
  const message = ollamaTestFailure('gemma3:4b');
  assert.match(message, /configured text model “gemma3:4b” is installed/);
  assert.match(message, /service is running/);
  assert.match(message, /Code and vision models were not tested/);
  assert.doesNotMatch(message, /not installed|connection refused|provider_initialization_failed/);
  assert.doesNotMatch(ollamaTestFailure('untrusted model\ntrace'), /untrusted|trace/);
  assert.ok(ollamaTestFailure('x'.repeat(129)).length < 300);
});

// Exercise the real component with React's server renderer; no browser or added dependency.
const componentUrl = new URL('../../src/components/settings/AIProvidersCard.tsx', import.meta.url);
const componentJs = ts.transpileModule(readFileSync(componentUrl, 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.React },
}).outputText.replace(/from ['"]([^'"]+)['"]/g, (_match, specifier) => {
  const url = specifier.startsWith('.')
    ? new URL(`${specifier}.ts`, componentUrl).href
    : import.meta.resolve(specifier);
  return `from ${JSON.stringify(url)}`;
});
const { AIProvidersCard } = await import(`data:text/javascript,${encodeURIComponent(componentJs)}`);
const baseProps = {
  showAIProviderSettings: true, providers: { ollama }, primaryProvider: 'ollama', fallbackProvider: null,
  loadingProviders: false, providerStateReady: true, providerLoadError: false,
  testingProvider: null, configuringProvider: null, providerMutationPending: false, modelDraftVersion: 0,
  onTestProvider: async () => {}, onSetPrimary: async () => {}, onSetFallback: async () => {},
  onConfigureProvider: async () => true, onDisableAI: async () => {}, onRetry: async () => {},
};
const render = overrides => renderToStaticMarkup(React.createElement(AIProvidersCard, { ...baseProps, ...overrides }));

test('rendered model controls expose three labeled exact values with the server length limit', () => {
  const html = render({});
  for (const [field, value] of Object.entries(models)) {
    assert.match(html, new RegExp(`<label[^>]+for="ollama-${field}"`));
    assert.match(html, new RegExp(`<input[^>]+id="ollama-${field}"[^>]+maxLength="128"[^>]+value="${value.replaceAll('.', '\\.')}"`));
    assert.match(html, new RegExp(`<dd[^>]*>${value.replaceAll('.', '\\.')}</dd>`));
  }
  assert.match(html, /Test text model/);
  assert.match(html, /does not download models or run a test/);
  assert.match(html, /Select a primary provider to use workspace AI\. A fallback is used only if the primary fails\./);
  assert.match(html, /Workspace AI has a selected primary provider/);
  assert.doesNotMatch(html, /Enable Ollama|Llama 3.2/);
});

test('rendered load and authorization gates expose no editable model or disable action', () => {
  for (const overrides of [{ loadingProviders: true }, { providerStateReady: false }, { providerLoadError: true }, { showAIProviderSettings: false }]) {
    const html = render(overrides);
    assert.doesNotMatch(html, /id="ollama-textModel"|Disable workspace AI/);
  }
});

test('rendered controls lock during mutations and disabled status requires both null selections', () => {
  const busy = render({ providerMutationPending: true, configuringProvider: 'ollama' });
  assert.match(busy, /<fieldset disabled=""/);
  assert.match(busy, /id="provider-ollama-configure" disabled="" aria-busy="true"/);
  assert.match(busy, /<button[^>]+disabled=""[^>]*>Disable workspace AI<\/button>/);
  const disabled = render({ primaryProvider: null, fallbackProvider: null });
  assert.match(disabled, /Workspace AI is disabled/);
  assert.match(disabled, /<button[^>]+disabled=""[^>]*>Disable workspace AI<\/button>/);
  assert.match(disabled, /Configured/);
  const fallbackOnly = render({ primaryProvider: null, fallbackProvider: 'ollama' });
  assert.doesNotMatch(fallbackOnly, /Workspace AI is disabled/);
  assert.match(fallbackOnly, /A fallback is saved; select a primary to use workspace AI\./);
  assert.match(fallbackOnly, /<button type="button" class="[^"]*">Disable workspace AI<\/button>/);
});
