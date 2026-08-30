import React, { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy, Key, Loader2, RefreshCw, Trash2, X } from 'lucide-react';
import { apiKeysApi } from '../../api/apiKeys';
import type { APIKeyMetadata } from '../../api/apiKeys';
import { useAuth } from '../../context/auth-context';

function errorMessage(error: unknown, fallback: string): string {
  const candidate = error as { response?: { data?: { detail?: string } } };
  return candidate.response?.data?.detail || fallback;
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'Never';
}

export function APIKeyManagementCard(): React.ReactElement {
  const { authMethod, logout } = useAuth();
  const [keys, setKeys] = useState<APIKeyMetadata[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revealRef = useRef<HTMLDivElement>(null);

  const loadKeys = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setKeys(await apiKeysApi.list());
    } catch (requestError) {
      setError(errorMessage(requestError, 'Could not load API keys.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadKeys(), 0);
    return () => window.clearTimeout(timer);
  }, [loadKeys]);

  const createKey = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) return;
    setCreating(true);
    setError(null);
    setCopied(false);
    try {
      const result = await apiKeysApi.create({ name: trimmedName });
      setCreatedKey(result.full_key);
      setName('');
      await loadKeys();
      requestAnimationFrame(() => revealRef.current?.focus());
    } catch (requestError) {
      setError(errorMessage(requestError, 'Could not create API key.'));
    } finally {
      setCreating(false);
    }
  };

  const copyCreatedKey = async (): Promise<void> => {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey);
      setCopied(true);
    } catch {
      setError('Could not copy the key. Select and copy it manually.');
    }
  };

  const revokeKey = async (key: APIKeyMetadata): Promise<void> => {
    if (!window.confirm(`Revoke API key “${key.name}”? This cannot be undone.`)) return;
    setRevoking(key.id);
    setError(null);
    try {
      const result = await apiKeysApi.revoke(key.id);
      setKeys(current => current.map(item => item.id === key.id ? { ...item, is_active: false } : item));
      if (result.revoked_current_key && authMethod === 'api_key') {
        await logout();
      }
    } catch (requestError) {
      setError(errorMessage(requestError, 'Could not revoke API key.'));
    } finally {
      setRevoking(null);
    }
  };

  return (
    <section className="card mb-6" aria-labelledby="api-key-management-title">
      <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 id="api-key-management-title" className="text-xl font-semibold text-primary flex items-center gap-2">
              <Key className="w-5 h-5" aria-hidden="true" /> API Key Management
            </h2>
            <p className="text-sm text-tertiary mt-1">Create credentials for CLI and programmatic access. Keys are shown in full only once.</p>
          </div>
          <button type="button" onClick={() => void loadKeys()} disabled={loading} className="btn-secondary px-3 py-2 flex items-center gap-2" aria-label="Refresh keys">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" /> Refresh
          </button>
        </div>
      </div>
      <div className="px-6 py-5 space-y-5">
        <div aria-live="polite" className="sr-only">{loading ? 'Loading API keys' : `${keys.length} API keys loaded`}</div>
        {error && <div role="alert" className="p-3 rounded-lg border text-sm" style={{ color: 'var(--content-error)', borderColor: 'var(--content-error)' }}>{error}</div>}

        {createdKey && (
          <div ref={revealRef} tabIndex={-1} role="status" className="p-4 rounded-lg border" style={{ borderColor: 'var(--content-warning)', backgroundColor: 'var(--surface-warning-subtle)' }}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-primary">Copy this key now — it will not be shown again.</p>
                <code className="block mt-2 p-3 rounded border break-all select-all text-sm" style={{ backgroundColor: 'var(--surface-primary)' }}>{createdKey}</code>
                <button type="button" onClick={() => void copyCreatedKey()} className="btn-primary mt-3 px-3 py-2 flex items-center gap-2" aria-label="Copy key">
                  {copied ? <Check className="w-4 h-4" aria-hidden="true" /> : <Copy className="w-4 h-4" aria-hidden="true" />}{copied ? 'Copied' : 'Copy key'}
                </button>
              </div>
              <button type="button" onClick={() => setCreatedKey(null)} className="btn-secondary p-2" aria-label="Dismiss key"><X className="w-4 h-4" aria-hidden="true" /></button>
            </div>
          </div>
        )}

        <form onSubmit={createKey} className="flex flex-col sm:flex-row gap-3 items-end">
          <div className="flex-1 w-full">
            <label htmlFor="new-api-key-name" className="text-sm font-medium text-secondary block mb-1">Key name</label>
            <input id="new-api-key-name" value={name} onChange={event => setName(event.target.value)} minLength={1} maxLength={100} required className="input w-full" placeholder="e.g. Accessibility CLI" />
          </div>
          <button type="submit" disabled={creating || !name.trim()} className="btn-primary px-4 py-2 flex items-center gap-2">
            {creating && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />} Create API key
          </button>
        </form>

        {loading ? (
          <div className="py-6 flex justify-center"><Loader2 className="w-5 h-5 animate-spin" aria-label="Loading API keys" /></div>
        ) : keys.length === 0 ? (
          <p className="text-sm text-tertiary">No API keys. Create one when you need programmatic access.</p>
        ) : (
          <div className="overflow-x-auto" style={{ contain: 'layout paint' }}>
            <table className="w-full text-sm">
              <caption className="sr-only">Your API keys and their status</caption>
              <thead><tr className="text-left text-tertiary"><th scope="col" className="py-2">Name</th><th scope="col">Prefix</th><th scope="col">Created</th><th scope="col">Last used</th><th scope="col">Expires</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{keys.map(key => (
                <tr key={key.id} className="border-t" style={{ borderColor: 'var(--border-subtle)' }}>
                  <td className="py-3 font-medium text-primary">{key.name}</td><td><code>{key.key_prefix}…</code></td><td>{formatDate(key.created_at)}</td><td>{formatDate(key.last_used_at)}</td><td>{formatDate(key.expires_at)}</td><td>{key.is_active ? 'Active' : 'Revoked'}</td>
                  <td className="text-right"><button type="button" onClick={() => void revokeKey(key)} disabled={!key.is_active || revoking === key.id} className="btn-secondary p-2" aria-label={`Revoke ${key.name}`}>{revoking === key.id ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Trash2 className="w-4 h-4" aria-hidden="true" />}</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
