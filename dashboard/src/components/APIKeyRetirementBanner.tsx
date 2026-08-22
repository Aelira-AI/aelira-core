import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/auth-context';

const RETIREMENT_NOTICE_VERSION = 'v1';

function SessionRetirementBanner({ userId }: { userId: string }): React.ReactElement | null {
  const storageKey = `aelira_api_key_retirement_ack:${RETIREMENT_NOTICE_VERSION}:${userId}`;
  const [dismissed, setDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(storageKey) === '1';
    } catch {
      return false;
    }
  });

  if (dismissed) return null;

  const dismiss = (): void => {
    setDismissed(true);
    try {
      localStorage.setItem(storageKey, '1');
    } catch {
      // The current render remains dismissed. A storage-denied browser may
      // show the notice again in a later session, which is the safe fallback.
    }
  };

  return (
    <div role="alert" className="border-b px-4 py-3" style={{ backgroundColor: 'var(--surface-warning-subtle)', borderColor: 'var(--content-warning)' }}>
      <div className="mx-auto max-w-7xl flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 mt-0.5 shrink-0" aria-hidden="true" style={{ color: 'var(--content-warning)' }} />
          <p className="text-sm text-primary">
            Legacy dashboard API keys have been retired. Create separate programmatic keys in{' '}
            <Link to="/settings" className="font-semibold underline">Settings</Link> if you use the CLI or integrations.
          </p>
        </div>
        <button type="button" onClick={dismiss} className="btn-secondary px-4 py-2 shrink-0">I understand</button>
      </div>
    </div>
  );
}

export function APIKeyRetirementBanner(): React.ReactElement | null {
  const { authMethod, user } = useAuth();
  if (authMethod !== 'session' || !user) return null;
  return <SessionRetirementBanner key={`${RETIREMENT_NOTICE_VERSION}:${user.id}`} userId={user.id} />;
}
