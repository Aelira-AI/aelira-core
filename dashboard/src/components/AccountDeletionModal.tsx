import React, { useState, useEffect, useRef } from 'react';
import { AlertTriangle, Mail, Loader2, X } from 'lucide-react';
import { useToast } from '../context/toast-context';
import { accountApi } from '../api/account';

interface AccountDeletionModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDeleted: () => void;
}

type Step = 'warning' | 'code';

export function AccountDeletionModal({
  isOpen,
  onClose,
  onDeleted,
}: AccountDeletionModalProps): React.ReactElement | null {
  const [step, setStep] = useState<Step>('warning');
  const [confirmText, setConfirmText] = useState('');
  const [code, setCode] = useState('');
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(false);
  const [codeExpiresAt, setCodeExpiresAt] = useState<Date | null>(null);
  const [timeRemaining, setTimeRemaining] = useState<number>(0);
  const { showToast } = useToast();
  const codeInputRef = useRef<HTMLInputElement>(null);

  // Reset state when modal opens/closes
  useEffect(() => {
    if (!isOpen) {
      setStep('warning');
      setConfirmText('');
      setCode('');
      setReason('');
      setLoading(false);
      setCodeExpiresAt(null);
    }
  }, [isOpen]);

  // Countdown timer for code expiry
  useEffect(() => {
    if (!codeExpiresAt) return;

    const interval = setInterval(() => {
      const remaining = Math.max(
        0,
        Math.floor((codeExpiresAt.getTime() - Date.now()) / 1000)
      );
      setTimeRemaining(remaining);

      if (remaining <= 0) {
        clearInterval(interval);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [codeExpiresAt]);

  // Focus code input when step changes
  useEffect(() => {
    if (step === 'code' && codeInputRef.current) {
      codeInputRef.current.focus();
    }
  }, [step]);

  if (!isOpen) return null;

  const handleSendCode = async (): Promise<void> => {
    if (confirmText !== 'DELETE') return;

    try {
      setLoading(true);
      const response = await accountApi.requestDeletionCode();
      setCodeExpiresAt(new Date(response.code_expires_at));
      setStep('code');
      showToast('Confirmation code sent to your email.', 'info');
    } catch (error) {
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(
        err.response?.data?.detail || 'Failed to send confirmation code.',
        'error'
      );
    } finally {
      setLoading(false);
    }
  };

  const handleConfirmDeletion = async (): Promise<void> => {
    if (code.length !== 6) return;

    try {
      setLoading(true);
      await accountApi.confirmDeletion(code, reason || undefined);
      showToast(
        'Account deletion scheduled. Your data will be permanently removed after 30 days.',
        'warning'
      );
      onDeleted();
    } catch (error) {
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(
        err.response?.data?.detail || 'Failed to confirm deletion.',
        'error'
      );
    } finally {
      setLoading(false);
    }
  };

  const handleResendCode = async (): Promise<void> => {
    try {
      setLoading(true);
      const response = await accountApi.requestDeletionCode();
      setCodeExpiresAt(new Date(response.code_expires_at));
      setCode('');
      showToast('New confirmation code sent.', 'info');
    } catch (error) {
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(
        err.response?.data?.detail || 'Failed to resend code.',
        'error'
      );
    } finally {
      setLoading(false);
    }
  };

  const handleBackdropClick = (e: React.MouseEvent): void => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  const formatTime = (seconds: number): string => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.5)' }}
      onClick={handleBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-label="Delete account"
    >
      <div
        className="w-full max-w-md rounded-xl shadow-xl"
        style={{ backgroundColor: 'var(--surface-primary)' }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 border-b"
          style={{ borderColor: 'var(--border-subtle)' }}
        >
          <div className="flex items-center gap-2">
            <AlertTriangle
              className="w-5 h-5"
              style={{ color: 'var(--content-error)' }}
            />
            <h2 className="text-lg font-semibold text-primary">
              Delete Account
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg transition-colors hover:bg-gray-100"
            aria-label="Close"
          >
            <X className="w-5 h-5 text-tertiary" />
          </button>
        </div>

        {/* Step 1: Warning + Type DELETE */}
        {step === 'warning' && (
          <div className="px-6 py-5">
            <div
              className="rounded-lg p-4 mb-4"
              style={{
                backgroundColor: 'var(--surface-error-subtle, #fef2f2)',
                border: '1px solid var(--content-error)',
              }}
            >
              <p
                className="text-sm font-medium mb-2"
                style={{ color: 'var(--content-error)' }}
              >
                This action cannot be undone.
              </p>
              <ul
                className="text-xs space-y-1"
                style={{ color: 'var(--content-error)' }}
              >
                <li>Your account will be deactivated immediately</li>
                <li>All data will be permanently deleted after 30 days</li>
                <li>You will not be able to re-register with this email</li>
                <li>Any billing set up by your deployment will be ended</li>
              </ul>
            </div>

            <label className="block text-sm text-secondary mb-2">
              Type <strong>DELETE</strong> to continue:
            </label>
            <input
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="DELETE"
              className="input w-full mb-3"
              autoComplete="off"
              spellCheck={false}
            />

            <label className="block text-sm text-secondary mb-2">
              Reason for leaving (optional):
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Help us improve..."
              className="input w-full mb-4"
              rows={2}
              maxLength={500}
            />

            <div className="flex gap-3">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 rounded-lg border transition-colors"
                style={{
                  borderColor: 'var(--border-primary)',
                  color: 'var(--content-primary)',
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleSendCode}
                disabled={confirmText !== 'DELETE' || loading}
                className="flex-1 px-4 py-2 rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                style={{
                  backgroundColor: confirmText === 'DELETE' ? 'var(--content-error)' : 'var(--surface-secondary)',
                  color: confirmText === 'DELETE' ? 'var(--content-inverse)' : 'var(--content-tertiary)',
                }}
              >
                {loading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Mail className="w-4 h-4" />
                )}
                Send Code
              </button>
            </div>
          </div>
        )}

        {/* Step 2: Enter 6-digit code */}
        {step === 'code' && (
          <div className="px-6 py-5">
            <div className="text-center mb-4">
              <Mail
                className="w-10 h-10 mx-auto mb-2"
                style={{ color: 'var(--accent-primary)' }}
              />
              <p className="text-sm text-secondary">
                Enter the 6-digit code sent to your email.
              </p>
              {timeRemaining > 0 && (
                <p className="text-xs text-tertiary mt-1">
                  Code expires in{' '}
                  <span className="font-mono font-medium">
                    {formatTime(timeRemaining)}
                  </span>
                </p>
              )}
              {timeRemaining === 0 && codeExpiresAt && (
                <p
                  className="text-xs mt-1"
                  style={{ color: 'var(--content-error)' }}
                >
                  Code expired.{' '}
                  <button
                    onClick={handleResendCode}
                    className="underline font-medium"
                    disabled={loading}
                  >
                    Resend
                  </button>
                </p>
              )}
            </div>

            <input
              ref={codeInputRef}
              type="text"
              value={code}
              onChange={(e) => {
                const val = e.target.value.replace(/\D/g, '').slice(0, 6);
                setCode(val);
              }}
              placeholder="000000"
              className="input w-full text-center text-2xl font-mono tracking-widest mb-4"
              maxLength={6}
              inputMode="numeric"
              autoComplete="one-time-code"
            />

            <div className="flex gap-3">
              <button
                onClick={() => setStep('warning')}
                className="flex-1 px-4 py-2 rounded-lg border transition-colors"
                style={{
                  borderColor: 'var(--border-primary)',
                  color: 'var(--content-primary)',
                }}
              >
                Back
              </button>
              <button
                onClick={handleConfirmDeletion}
                disabled={code.length !== 6 || loading || timeRemaining === 0}
                className="flex-1 px-4 py-2 rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                style={{
                  backgroundColor: code.length === 6 ? 'var(--content-error)' : 'var(--surface-secondary)',
                  color: code.length === 6 ? 'var(--content-inverse)' : 'var(--content-tertiary)',
                }}
              >
                {loading && <Loader2 className="w-4 h-4 animate-spin" />}
                Confirm Deletion
              </button>
            </div>

            {timeRemaining > 0 && (
              <div className="text-center mt-3">
                <button
                  onClick={handleResendCode}
                  className="text-xs underline text-tertiary hover:text-secondary"
                  disabled={loading}
                >
                  Resend code
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
