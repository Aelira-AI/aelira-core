import React, { useState, useRef } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import { useAuth } from '../context/auth-context';
import { apiClient } from '../api/client';
import { Logo } from '../components/Logo';
import { trackEvent } from '../utils/analytics';
import { Loader2, CheckCircle, XCircle, Mail } from 'lucide-react';

type VerificationStatus = 'ready' | 'verifying' | 'success' | 'error';

interface VerifyResponse {
  success: boolean;
  message?: string;
}

export function VerifyMagicLink(): React.ReactElement {
  // Start with 'ready' to require user click (prevents prefetch consuming token)
  const [status, setStatus] = useState<VerificationStatus>('ready');
  const [error, setError] = useState<string>('');
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { validateSession } = useAuth();

  // Prevent duplicate verification calls
  const verificationAttempted = useRef<boolean>(false);

  const email = searchParams.get('email');
  const token = searchParams.get('token');

  // Check for missing params on render
  const hasValidParams = email && token;

  const handleVerify = async (): Promise<void> => {
    // Guard against duplicate calls
    if (verificationAttempted.current) {
      return;
    }
    verificationAttempted.current = true;
    setStatus('verifying');

    if (!email || !token) {
      setStatus('error');
      setError('Invalid magic link. Please request a new one.');
      return;
    }

    try {
      // Call the API to verify the magic link (POST to prevent prefetch consumption)
      // This endpoint sets session cookies on success
      const response = await apiClient.post<VerifyResponse>('/auth/magic-link/verify', {
        email,
        token
      });

      if (response.data.success) {
        setStatus('success');
        trackEvent('dash-magic-link-verified', {});

        // Refresh the auth context to pick up the new session
        await validateSession();

        // Redirect to dashboard after a brief delay
        setTimeout(() => {
          navigate('/dashboard', { replace: true });
        }, 1500);
      } else {
        setStatus('error');
        setError(response.data.message || 'Verification failed. Please try again.');
      }
    } catch (err: unknown) {
      setStatus('error');
      const axiosError = err as { response?: { data?: { detail?: string } } };
      const errorMessage = axiosError.response?.data?.detail ||
                          'Invalid or expired magic link. Please request a new one.';
      setError(errorMessage);
    }
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{
        background: 'linear-gradient(135deg, var(--surface-accent-subtle) 0%, var(--surface-accent) 100%)'
      }}
    >
      <div className="w-full max-w-md">
        <div className="card text-center">
          <div className="flex justify-center mb-6">
            <Logo width={200} height={60} />
          </div>

          {status === 'ready' && hasValidParams && (
            <>
              <Mail className="w-12 h-12 mx-auto mb-4" style={{ color: 'var(--accent-primary)' }} />
              <h1 className="text-xl font-bold text-primary mb-2">Complete Sign In</h1>
              <p className="text-secondary mb-6">Click below to verify your email and sign in to Aelira.</p>
              <button
                onClick={handleVerify}
                className="btn-primary w-full flex items-center justify-center gap-2"
              >
                <CheckCircle className="w-5 h-5" />
                Verify & Sign In
              </button>
            </>
          )}

          {status === 'ready' && !hasValidParams && (
            <>
              <XCircle className="w-12 h-12 mx-auto mb-4" style={{ color: 'var(--content-error)' }} />
              <h1 className="text-xl font-bold text-primary mb-2">Invalid Link</h1>
              <p className="text-secondary mb-6">This magic link is missing required parameters.</p>
              <Link
                to="/login"
                className="btn-primary inline-block"
              >
                Back to Login
              </Link>
            </>
          )}

          {status === 'verifying' && (
            <>
              <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin" style={{ color: 'var(--accent-primary)' }} />
              <h1 className="text-xl font-bold text-primary mb-2">Verifying...</h1>
              <p className="text-secondary">Please wait while we sign you in.</p>
            </>
          )}

          {status === 'success' && (
            <>
              <CheckCircle className="w-12 h-12 mx-auto mb-4" style={{ color: 'var(--content-success)' }} />
              <h1 className="text-xl font-bold text-primary mb-2">Signed In!</h1>
              <p className="text-secondary">Redirecting to your dashboard...</p>
            </>
          )}

          {status === 'error' && (
            <>
              <XCircle className="w-12 h-12 mx-auto mb-4" style={{ color: 'var(--content-error)' }} />
              <h1 className="text-xl font-bold text-primary mb-2">Verification Failed</h1>
              <p className="text-secondary mb-6">{error}</p>
              <Link
                to="/login"
                className="btn-primary inline-block"
              >
                Back to Login
              </Link>
            </>
          )}
        </div>

        <div className="mt-6 text-center text-sm text-tertiary">
          <p>© 2026 Aelira. All rights reserved.</p>
        </div>
      </div>
    </div>
  );
}
