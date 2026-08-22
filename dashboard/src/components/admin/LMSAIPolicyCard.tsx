import React, { FormEvent, useEffect, useRef, useState } from 'react';
import { Loader, RefreshCw, ShieldCheck } from 'lucide-react';
import { adminApi } from '../../api/admin';
import type { LMSAIPolicy, LMSAIPolicyUpdate, LMSAIProvider } from '../../api/admin';

const providers: Array<{ value: LMSAIProvider; label: string }> = [
  { value: 'ollama', label: 'Ollama (local)' }, { value: 'gemini', label: 'Gemini' },
  { value: 'openai', label: 'OpenAI' }, { value: 'anthropic', label: 'Anthropic' },
  { value: 'xai', label: 'xAI' },
];
const reasonText: Record<string, string> = {
  ready: 'Ready', credentials_missing: 'Configure a matching department key first',
  credential_provider_mismatch: 'The configured key belongs to another provider',
  credential_invalid: 'Reconfigure the department key; the stored credential cannot be decrypted',
  pilot_not_approved: 'Gemini pilot access is not approved', platform_key_missing: 'The platform Gemini key is unavailable',
  credentials_forbidden: 'Ollama cannot use cloud credentials', ambient_key_forbidden: 'Remove the ambient Ollama API key',
  host_not_loopback: 'Ollama must use a loopback host', unreachable: 'The local Ollama service is unreachable',
  model_missing: 'Required local models are not installed',
};

function editable(policy: LMSAIPolicy): LMSAIPolicyUpdate {
  return { enabled: policy.enabled, provider: policy.provider, remediation_enabled: policy.remediation_enabled,
    alt_text_enabled: policy.alt_text_enabled, expected_revision: policy.policy_revision };
}

export function LMSAIPolicyCard(): React.ReactElement {
  const [policy, setPolicy] = useState<LMSAIPolicy | null>(null);
  const [form, setForm] = useState<LMSAIPolicyUpdate | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const errorSummaryRef = useRef<HTMLParagraphElement>(null);

  const load = async (): Promise<void> => {
    setLoading(true); setError(null);
    try { const current = await adminApi.getLMSAIPolicy(); setPolicy(current); setForm(editable(current)); setStatus('LMS AI policy loaded.'); }
    catch { setError('The LMS AI policy could not be loaded.'); }
    finally { setLoading(false); }
  };
  // The initial request intentionally owns independent loading/error state.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, []);

  const save = async (event: FormEvent): Promise<void> => {
    event.preventDefault(); if (!form) return; setSaving(true); setError(null);
    try { const current = await adminApi.updateLMSAIPolicy(form); setPolicy(current); setForm(editable(current)); setStatus(`LMS AI policy saved at revision ${current.policy_revision}.`); }
    catch (caught) {
      const failure = caught as { response?: { data?: { detail?: { code?: string; reason?: string; current?: LMSAIPolicy } } } };
      const detail = failure.response?.data?.detail;
      if (detail?.current && (detail.code === 'policy_revision_conflict' || detail?.code === 'provider_not_ready')) {
        setPolicy(detail.current); setForm(editable(detail.current));
        if (detail.code === 'provider_not_ready') {
          const reason = (detail.reason ? reasonText[detail.reason] : undefined) ?? 'The selected provider is unavailable';
          setError(`Provider is not ready. ${reason}. Update the provider configuration and try again.`);
          setStatus(`Policy refreshed at revision ${detail.current.policy_revision}. ${reason}.`);
        } else {
          setError('Another administrator changed this policy. Review the refreshed policy before saving again.');
          setStatus(`Current policy loaded at revision ${detail.current.policy_revision}.`);
        }
        window.setTimeout(() => errorSummaryRef.current?.focus(), 0);
      } else setError('The policy could not be saved. Check provider readiness and try again.');
    } finally { setSaving(false); }
  };

  if (loading) return <section className="card mb-8" aria-label="LMS AI policy" aria-busy="true"><Loader className="w-5 h-5 animate-spin" aria-hidden="true" /> Loading LMS AI policy…</section>;
  if (!policy || !form) return <section className="card mb-8" aria-label="LMS AI policy"><p role="alert">{error}</p><button className="btn-secondary mt-3" onClick={() => void load()}>Retry</button></section>;
  const valid = !form.enabled || (form.provider !== null && (form.remediation_enabled || form.alt_text_enabled));
  const conflict = error?.includes('Another administrator') ?? false;

  return <section className="card mb-8" aria-labelledby="lms-ai-policy-title">
    <div className="flex items-center gap-2 mb-2"><ShieldCheck className="w-5 h-5" aria-hidden="true" /><h2 id="lms-ai-policy-title" className="text-xl font-semibold text-primary">LMS AI policy</h2>{policy.pilot_gemini_approved && <span className="text-xs rounded px-2 py-1 bg-surface-tertiary">Gemini pilot approved</span>}</div>
    <p id="lms-ai-policy-description" className="text-sm text-secondary mb-4">Deterministic scans always run. This account-wide policy separately authorizes AI remediation and alternative text.</p>
    <div aria-live="polite" className="text-sm mb-3">{status}</div>
    {error && <p ref={errorSummaryRef} tabIndex={-1} role="alert" aria-live="assertive" className="text-sm mb-3" style={{ color: 'var(--content-error)' }}>{error}</p>}
    <form onSubmit={save} aria-describedby="lms-ai-policy-description"><fieldset disabled={saving} className="space-y-4"><legend className="sr-only">Account-wide LMS AI controls</legend>
      <label className="flex gap-3 items-start" htmlFor="lms-ai-enabled"><input id="lms-ai-enabled" type="checkbox" checked={form.enabled} aria-describedby="lms-ai-enabled-help" onChange={(e) => setForm({ ...form, enabled: e.target.checked, provider: e.target.checked ? form.provider : null, remediation_enabled: e.target.checked ? form.remediation_enabled : false, alt_text_enabled: e.target.checked ? form.alt_text_enabled : false })}/><span><strong>Enable AI for LMS content</strong><span id="lms-ai-enabled-help" className="block text-sm text-secondary">Master authorization; turning it off revokes new AI calls.</span></span></label>
      <div><label htmlFor="lms-ai-provider" className="block font-medium">Provider</label><p id="lms-ai-provider-help" className="text-sm text-secondary">Cloud providers send selected content outside your deployment. Ollama stays local and must use loopback without an API key.</p><select id="lms-ai-provider" className="input mt-2" value={form.provider ?? ''} disabled={!form.enabled || saving} aria-describedby="lms-ai-provider-help" onChange={(e) => setForm({ ...form, provider: (e.target.value || null) as LMSAIProvider | null })}><option value="">Select a provider</option>{providers.map(({ value, label }) => { const ready = policy.provider_readiness[value]; return <option key={value} value={value} disabled={!ready.ready}>{label} — {reasonText[ready.reason] ?? 'Unavailable'}</option>; })}</select></div>
      <fieldset disabled={!form.enabled || saving} className="space-y-2"><legend className="font-medium">Allowed purposes</legend>
        <label className="flex gap-2" htmlFor="lms-ai-remediation"><input id="lms-ai-remediation" type="checkbox" checked={form.remediation_enabled} aria-describedby="lms-ai-remediation-help" onChange={(e) => setForm({ ...form, remediation_enabled: e.target.checked })}/><span>Remediation<span id="lms-ai-remediation-help" className="block text-sm text-secondary">Generate suggested repairs for eligible LMS content.</span></span></label>
        <label className="flex gap-2" htmlFor="lms-ai-alt-text"><input id="lms-ai-alt-text" type="checkbox" checked={form.alt_text_enabled} aria-describedby="lms-ai-alt-text-help" onChange={(e) => setForm({ ...form, alt_text_enabled: e.target.checked })}/><span>Alternative text<span id="lms-ai-alt-text-help" className="block text-sm text-secondary">Send images to the selected provider for description.</span></span></label>
      </fieldset>
      {!valid && <p role="alert" style={{ color: 'var(--content-error)' }}>Choose a ready provider and at least one purpose.</p>}
      <div className="flex gap-3"><button type="submit" className="btn-primary" disabled={!valid || saving}>{saving ? 'Saving…' : 'Save policy'}</button><button type="button" className="btn-secondary" disabled={saving} onClick={() => setForm(editable(policy))}>Reset changes</button>{conflict && <button type="button" className="btn-secondary flex items-center gap-2" onClick={() => { setForm(editable(policy)); setStatus('Current policy reloaded.'); }}><RefreshCw className="w-4 h-4" aria-hidden="true" />Reload current policy</button>}</div>
    </fieldset></form>
  </section>;
}
