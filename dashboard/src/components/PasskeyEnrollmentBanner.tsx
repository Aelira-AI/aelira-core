import React, { useState, useEffect } from 'react';
import { KeyRound, CheckCircle, AlertCircle, X, Loader2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { isPasskeySupported, listPasskeys, registerPasskey } from '../api/passkey';

const ENROLLMENT_BANNER_VERSION = 'v1';

export function PasskeyEnrollmentBanner(): React.ReactElement | null {
  const { authMethod, user } = useAuth();
  const [supported] = useState<boolean>(() => isPasskeySupported());
  const [enrolledCount, setEnrolledCount] = useState<number | null>(null);
  const [enrolling, setEnrolling] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const storageKey = user ? `aelira_passkey_prompt_dismissed:${ENROLLMENT_BANNER_VERSION}:${user.id}` : '';
  const [dismissed, setDismissed] = useState<boolean>(() => {
    if (!user) return true;
    try {
      return localStorage.getItem(storageKey) === '1';
    } catch {
      return false;
    }
  });

  // Check existing credentials if supported
  useEffect(() => {
    if (!user || authMethod !== 'session' || dismissed || !supported) {
      return;
    }

    let isMounted = true;
    listPasskeys()
      .then((credentials) => {
        if (isMounted) {
          setEnrolledCount(credentials.length);
        }
      })
      .catch(() => {
        // Fail-safe: don't show the banner if credential lookup fails
        if (isMounted) {
          setEnrolledCount(null);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [user, authMethod, dismissed, supported]);

  if (!user || authMethod !== 'session' || dismissed || !supported || enrolledCount === null || enrolledCount > 0) {
    return null;
  }

  const dismiss = (): void => {
    setDismissed(true);
    try {
      localStorage.setItem(storageKey, '1');
    } catch {
      // storage unavailable safe fallback
    }
  };

  const handleRegister = async (): Promise<void> => {
    setEnrolling(true);
    setStatusMessage(null);
    try {
      const os = typeof navigator !== 'undefined' && navigator.userAgent.includes('Mac')
        ? 'Mac'
        : typeof navigator !== 'undefined' && navigator.userAgent.includes('Windows')
        ? 'Windows'
        : 'Device';
      const defaultName = `${os} Passkey`;
      const res = await registerPasskey(defaultName);
      if (res && res.success) {
        setStatusMessage({
          type: 'success',
          text: 'Passkey registered successfully! You can now sign in with one click next time.',
        });
        setEnrolledCount(1);
        setTimeout(() => {
          dismiss();
        }, 3500);
      }
    } catch (err: unknown) {
      const errorMsg = err instanceof Error ? err.message : 'Passkey registration cancelled or failed.';
      setStatusMessage({
        type: 'error',
        text: errorMsg,
      });
    } finally {
      setEnrolling(false);
    }
  };

  return (
    <div
      role="region"
      aria-label="Passkey enrollment recommendation"
      className="border-b px-4 py-3"
      style={{
        backgroundColor: 'var(--surface-info-subtle, rgba(59, 130, 246, 0.08))',
        borderColor: 'var(--accent-solid, #3B82F6)',
      }}
    >
      <div className="mx-auto max-w-7xl flex flex-col md:flex-row md:items-center justify-between gap-3">
        <div className="flex items-start gap-3">
          <KeyRound
            className="w-5 h-5 mt-0.5 shrink-0"
            aria-hidden="true"
            style={{ color: 'var(--accent-solid, #3B82F6)' }}
          />
          <div>
            <p className="text-sm text-primary">
              <span className="font-semibold">Sign in faster next time:</span> Set up a biometric passkey (Touch ID, Face ID, or Windows Hello) to sign in with one click without waiting for magic links.
            </p>
            {statusMessage && (
              <p
                role="status"
                className={`text-xs mt-1 flex items-center gap-1.5 ${
                  statusMessage.type === 'success' ? 'text-emerald-600 dark:text-emerald-400 font-medium' : 'text-red-600 dark:text-red-400'
                }`}
              >
                {statusMessage.type === 'success' ? (
                  <CheckCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                ) : (
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                )}
                {statusMessage.text}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0 self-end md:self-center">
          <button
            type="button"
            onClick={handleRegister}
            disabled={enrolling || statusMessage?.type === 'success'}
            className="btn-primary px-3.5 py-1.5 text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"
          >
            {enrolling && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />}
            {enrolling ? 'Enrolling...' : 'Set up Passkey'}
          </button>
          <Link
            to="/settings"
            className="text-xs text-secondary hover:underline px-2 py-1"
          >
            Manage in Settings
          </Link>
          <button
            type="button"
            onClick={dismiss}
            aria-label="Dismiss passkey recommendation"
            className="p-1 rounded text-secondary hover:text-primary hover:bg-black/5 dark:hover:bg-white/5"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}
