import React, { useState, useEffect, ChangeEvent, FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/auth-context';
import { useTheme } from '../context/theme-context';
import { apiClient } from '../api/client';
import { Key, Moon, Sun, Mail, ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { Logo } from '../components/Logo';
import { trackEvent } from '../utils/analytics';
import { buildAuthContinuationUrl, resolveSafeNext } from '../utils/safeNext';

interface IconProps {
  className?: string;
}

interface OAuthStatus {
  oauth_allowed: boolean;
  google_available: boolean;
  microsoft_available: boolean;
}

const SESSION_EXPIRED_MESSAGE = 'Your session has expired. Please sign in again.';

// Google icon component
function GoogleIcon({ className }: IconProps): React.ReactElement {
  return (
    <svg className={className} viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
    </svg>
  );
}

// Microsoft icon component
function MicrosoftIcon({ className }: IconProps): React.ReactElement {
  return (
    <svg className={className} viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path fill="#F25022" d="M1 1h10v10H1z"/>
      <path fill="#00A4EF" d="M1 13h10v10H1z"/>
      <path fill="#7FBA00" d="M13 1h10v10H13z"/>
      <path fill="#FFB900" d="M13 13h10v10H13z"/>
    </svg>
  );
}

export function Login(): React.ReactElement {
  const [email, setEmail] = useState<string>('');
  const [apiKey, setApiKey] = useState<string>('');
  const [error, setError] = useState<string>(() =>
    window.location.pathname === '/login' && window.location.search === '?expired=1'
      ? SESSION_EXPIRED_MESSAGE
      : ''
  );
  const [success, setSuccess] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [showApiKeyLogin, setShowApiKeyLogin] = useState<boolean>(false);
  const [oauthStatus, setOauthStatus] = useState<OAuthStatus | null>(null);
  const [checkingOauth, setCheckingOauth] = useState<boolean>(false);

  const { login } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const isExpiredLogin =
    window.location.pathname === '/login' && window.location.search === '?expired=1';

  // Check for OAuth errors or magic link verification
  useEffect(() => {
    const errorParam = searchParams.get('error');
    const messageParam = searchParams.get('message');

    if (errorParam) {
      switch (errorParam) {
        case 'oauth_denied':
          setError('OAuth login was cancelled.');
          break;
        case 'invalid_state':
          setError('Invalid OAuth state. Please try again.');
          break;
        case 'token_error':
          setError('Failed to authenticate with OAuth provider.');
          break;
        case 'tier_required':
          setError(messageParam || 'No account exists for this email. Ask your administrator for an invitation.');
          break;
        case 'no_email':
          setError('No email returned from OAuth provider.');
          break;
        default:
          setError('Authentication failed. Please try again.');
      }
    }
  }, [searchParams]);

  // Check OAuth availability when email changes
  useEffect(() => {
    const checkOauth = async (): Promise<void> => {
      if (!email || !email.includes('@')) {
        setOauthStatus(null);
        return;
      }

      setCheckingOauth(true);
      try {
        const response = await apiClient.get<OAuthStatus>('/auth/oauth/status', {
          params: { email }
        });
        setOauthStatus(response.data);
      } catch (err) {
        console.warn('Failed to check OAuth status:', err);
        setOauthStatus(null);
      } finally {
        setCheckingOauth(false);
      }
    };

    // Debounce the check
    const timeoutId = setTimeout(checkOauth, 500);
    return () => clearTimeout(timeoutId);
  }, [email]);

  const handleMagicLinkRequest = async (e: FormEvent<HTMLFormElement>): Promise<void> => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setLoading(true);

    try {
      trackEvent('dash-login-method', { method: 'magic_link' });
      await apiClient.post(
        '/auth/magic-link/request',
        { email, next: resolveSafeNext(searchParams.get('next')) },
        { _skipApiKeyAuth: true }
      );
      setSuccess('Check your email! We sent you a magic link to sign in.');
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } };
      const errorMessage = axiosError.response?.data?.detail ||
                          'Failed to send magic link. Please try again.';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleApiKeySubmit = async (e: FormEvent<HTMLFormElement>): Promise<void> => {
    e.preventDefault();
    setError('');
    setLoading(true);

    trackEvent('dash-login-method', { method: 'api_key' });
    const result = await login(apiKey);

    if (result.success) {
      navigate(resolveSafeNext(searchParams.get('next')));
    } else {
      setError(result.error || 'Login failed');
      setLoading(false);
    }
  };

  const handleGoogleLogin = (): void => {
    trackEvent('dash-login-method', { method: 'google' });
    window.location.href = buildAuthContinuationUrl(
      apiClient.defaults.baseURL,
      '/auth/google/login',
      searchParams.get('next')
    );
  };

  const handleMicrosoftLogin = (): void => {
    trackEvent('dash-login-method', { method: 'microsoft' });
    window.location.href = buildAuthContinuationUrl(
      apiClient.defaults.baseURL,
      '/auth/microsoft/login',
      searchParams.get('next')
    );
  };

  // Check if user already has a session
  useEffect(() => {
    if (isExpiredLogin) {
      return;
    }

    const checkSession = async (): Promise<void> => {
      try {
        const response = await apiClient.get<{ user?: unknown }>('/auth/session/validate', {
          _skipApiKeyAuth: true,
        });
        if (response.data.user) {
          navigate(resolveSafeNext(searchParams.get('next')));
        }
      } catch {
        // No valid session, stay on login page
      }
    };
    checkSession();
  }, [isExpiredLogin, navigate, searchParams]);

  return (
    <main
      className="min-h-screen flex items-center justify-center p-4"
      style={{
        background: 'linear-gradient(135deg, var(--surface-accent-subtle) 0%, var(--surface-accent) 100%)'
      }}
    >
      {/* Theme toggle button */}
      <button
        onClick={toggleTheme}
        className="fixed bottom-6 right-6 z-50 p-3 rounded-full shadow-lg hover:shadow-xl transition-shadow duration-200"
        style={{ backgroundColor: 'var(--surface-primary)' }}
        aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      >
        {theme === 'dark' ? (
          <Sun className="w-5 h-5" style={{ color: 'var(--content-warning)' }} />
        ) : (
          <Moon className="w-5 h-5" style={{ color: 'var(--accent)' }} />
        )}
      </button>

      <div className="w-full max-w-md">
        <div className="card">
          <div className="text-center mb-8">
            <div className="flex justify-center mb-6">
              <Logo width={200} height={60} />
            </div>
            <h1 className="text-2xl font-bold text-primary mb-2">Welcome Back</h1>
            <p className="text-secondary">Sign in with your email</p>
          </div>

          {/* Success message */}
          {success && (
            <div
              className="rounded-lg p-4 border mb-6"
              style={{
                backgroundColor: 'var(--surface-success-subtle)',
                borderColor: 'var(--content-success)',
                color: 'var(--content-success)'
              }}
              role="status"
              aria-live="polite"
            >
              <div className="flex items-center gap-3">
                <Mail className="w-5 h-5 shrink-0" aria-hidden="true" />
                <p className="text-sm">{success}</p>
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div
              className="rounded-lg p-3 border mb-6"
              style={{
                backgroundColor: 'var(--surface-error-subtle)',
                borderColor: 'var(--content-error)',
                color: 'var(--content-error)'
              }}
              role="alert"
            >
              <p className="text-sm">{error}</p>
            </div>
          )}

          {!success && (
            <>
              {/* Magic Link Form */}
              <form onSubmit={handleMagicLinkRequest} className="space-y-4">
                <div>
                  <label htmlFor="email" className="block text-sm font-medium text-secondary mb-2">
                    Email Address
                  </label>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e: ChangeEvent<HTMLInputElement>) => setEmail(e.target.value)}
                    className="input"
                    placeholder="you@university.edu"
                    required
                    disabled={loading}
                    autoComplete="email"
                  />
                </div>

                <button
                  type="submit"
                  className="btn-primary w-full flex items-center justify-center gap-2"
                  disabled={loading}
                >
                  {loading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                      Sending...
                    </>
                  ) : (
                    <>
                      <Mail className="w-4 h-4" aria-hidden="true" />
                      Send Magic Link
                    </>
                  )}
                </button>
              </form>

              {/* OAuth Buttons - shown if email is from allowed tier */}
              {oauthStatus?.oauth_allowed && (oauthStatus?.google_available || oauthStatus?.microsoft_available) && (
                <>
                  <div className="relative my-6">
                    <div className="absolute inset-0 flex items-center">
                      <div className="w-full border-t" style={{ borderColor: 'var(--border-secondary)' }}></div>
                    </div>
                    <div className="relative flex justify-center text-sm">
                      <span className="px-2 text-tertiary" style={{ backgroundColor: 'var(--surface-primary)' }}>
                        Or continue with
                      </span>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    {oauthStatus.google_available && (
                      <button
                        type="button"
                        onClick={handleGoogleLogin}
                        className="btn-secondary flex items-center justify-center gap-2"
                        disabled={loading}
                      >
                        <GoogleIcon className="w-5 h-5" aria-hidden="true" />
                        Google
                      </button>
                    )}
                    {oauthStatus.microsoft_available && (
                      <button
                        type="button"
                        onClick={handleMicrosoftLogin}
                        className="btn-secondary flex items-center justify-center gap-2"
                        disabled={loading}
                      >
                        <MicrosoftIcon className="w-5 h-5" aria-hidden="true" />
                        Microsoft
                      </button>
                    )}
                  </div>
                </>
              )}

              {/* OAuth not available message */}
              {oauthStatus && !oauthStatus.oauth_allowed && email.includes('@') && !checkingOauth && (
                <p className="text-xs text-tertiary mt-4 text-center">
                  OAuth login is available for department and university accounts. Ask your
                  administrator to enable SSO in this workspace's configuration.
                </p>
              )}

              {/* API Key Login (collapsible) */}
              <div className="mt-6">
                <button
                  type="button"
                  onClick={() => setShowApiKeyLogin(!showApiKeyLogin)}
                  className="flex items-center justify-center gap-2 w-full text-sm text-tertiary hover:text-secondary transition-colors"
                  aria-expanded={showApiKeyLogin}
                  aria-controls="api-key-form"
                >
                  <Key className="w-4 h-4" aria-hidden="true" />
                  Use API Key instead
                  {showApiKeyLogin ? (
                    <ChevronUp className="w-4 h-4" aria-hidden="true" />
                  ) : (
                    <ChevronDown className="w-4 h-4" aria-hidden="true" />
                  )}
                </button>

                {showApiKeyLogin && (
                  <form id="api-key-form" onSubmit={handleApiKeySubmit} className="mt-4 space-y-4">
                    <div>
                      <label htmlFor="apiKey" className="block text-sm font-medium text-secondary mb-2">
                        API Key
                      </label>
                      <input
                        id="apiKey"
                        type="password"
                        value={apiKey}
                        onChange={(e: ChangeEvent<HTMLInputElement>) => setApiKey(e.target.value)}
                        className="input"
                        placeholder="aelira_live_..."
                        required
                        disabled={loading}
                      />
                    </div>

                    <button
                      type="submit"
                      className="btn-secondary w-full flex items-center justify-center gap-2"
                      disabled={loading}
                    >
                      {loading ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                          Signing in...
                        </>
                      ) : (
                        <>
                          <Key className="w-4 h-4" aria-hidden="true" />
                          Sign in with API Key
                        </>
                      )}
                    </button>
                  </form>
                )}
              </div>
            </>
          )}

          {/* Provisioning note */}
          <div className="mt-6 text-center text-sm text-secondary">
            <p>Don't have an account? Ask your administrator for an invitation.</p>
          </div>
        </div>

        <div className="mt-6 text-center text-sm text-tertiary">
          <p>© 2026 Aelira. All rights reserved.</p>
        </div>
      </div>
    </main>
  );
}
