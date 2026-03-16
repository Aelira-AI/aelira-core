import React, { useState, useEffect, ChangeEvent, FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTheme } from '../context/ThemeContext';
import { AlertCircle, Mail, Moon, Sun, ArrowRight, Sparkles, Zap, Crown, LucideIcon } from 'lucide-react';
import { Logo } from '../components/Logo';
import { trackEvent } from '../components/Analytics';

const API_BASE = import.meta.env.VITE_API_URL || 'https://api.aelira.ai';
const WEBSITE_URL = import.meta.env.VITE_WEBSITE_URL || 'http://localhost:3000';

interface TierDetails {
  name: string;
  price: string;
  Icon: LucideIcon;
  color: string;
  features: string[];
}

type TierKey = 'plus' | 'pro';

// Tier info for upgrade prompts
const TIER_INFO: Record<TierKey, TierDetails> = {
  plus: {
    name: 'Faculty Plus',
    price: '$29/mo',
    Icon: Zap,
    color: 'var(--content-accent)',
    features: ['50 documents/month', 'LaTeX/MathML support', 'Priority email support'],
  },
  pro: {
    name: 'Faculty Pro',
    price: '$79/mo',
    Icon: Crown,
    color: 'var(--content-warning)',
    features: ['Unlimited documents', 'Video transcription', 'API access'],
  },
};

interface ApiErrorDetail {
  msg?: string;
  message?: string;
}

interface ApiErrorResponse {
  detail?: string | ApiErrorDetail[] | ApiErrorDetail;
}

export function Signup(): React.ReactElement {
  const [searchParams] = useSearchParams();
  const [name, setName] = useState<string>('');
  const [institution, setInstitution] = useState<string>('');
  const [email, setEmail] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [emailSent, setEmailSent] = useState<boolean>(false);
  const { theme, toggleTheme } = useTheme();

  // Check for tier parameter from pricing page
  const requestedTier = searchParams.get('tier') as TierKey | null;
  const tierInfo = requestedTier && TIER_INFO[requestedTier] ? TIER_INFO[requestedTier] : null;

  // Store requested tier in localStorage for post-login redirect
  useEffect(() => {
    if (requestedTier && TIER_INFO[requestedTier]) {
      localStorage.setItem('aelira_requested_tier', requestedTier);
    }
  }, [requestedTier]);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>): Promise<void> => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      trackEvent('dash-signup-submit', { has_tier_intent: !!requestedTier });

      const response = await fetch(`${API_BASE}/auth/magic-link/request`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, name, institution }),
      });

      const data: ApiErrorResponse = await response.json();

      if (!response.ok) {
        // Handle validation errors (e.g., non-.edu email)
        if (response.status === 422) {
          const detail = data.detail;
          if (Array.isArray(detail)) {
            setError(detail[0]?.msg || 'Invalid email address');
          } else if (typeof detail === 'string') {
            setError(detail);
          } else if (detail && typeof detail === 'object') {
            setError(detail.message || 'Invalid email address');
          } else {
            setError('Invalid email address');
          }
        } else if (response.status === 429) {
          setError('Too many requests. Please try again later.');
        } else {
          setError(typeof data.detail === 'string' ? data.detail : 'Something went wrong. Please try again.');
        }
        return;
      }

      // Success - show email sent screen
      setEmailSent(true);
    } catch (err: unknown) {
      const fetchError = err as Error;
      setError(fetchError.message || 'Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async (): Promise<void> => {
    setLoading(true);
    setError(null);

    try {
      await fetch(`${API_BASE}/auth/magic-link/request`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email }),
      });
    } catch {
      // Silently fail - user can try again
    } finally {
      setLoading(false);
    }
  };

  // Floating theme toggle button
  const ThemeToggleButton = (): React.ReactElement => (
    <button
      onClick={toggleTheme}
      className="fixed bottom-6 right-6 z-50 p-3 rounded-full shadow-lg hover:shadow-xl transition-shadow duration-200"
      style={{ backgroundColor: 'var(--surface-primary)' }}
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
    >
      {theme === 'dark' ? (
        <Sun className="w-5 h-5" style={{ color: 'var(--content-warning)' }} />
      ) : (
        <Moon className="w-5 h-5" style={{ color: 'var(--accent-primary)' }} />
      )}
    </button>
  );

  // Email sent state
  if (emailSent) {
    return (
      <div
        className="min-h-screen flex items-center justify-center p-4"
        style={{
          background: 'linear-gradient(135deg, var(--surface-accent-subtle) 0%, var(--surface-accent) 100%)'
        }}
      >
        <ThemeToggleButton />
        <div className="w-full max-w-md">
          <div className="card text-center">
            <div className="flex justify-center mb-6">
              <div
                className="w-16 h-16 rounded-full flex items-center justify-center"
                style={{ backgroundColor: 'var(--surface-success-subtle)' }}
              >
                <Mail className="w-8 h-8" style={{ color: 'var(--content-success)' }} />
              </div>
            </div>

            <h1 className="text-2xl font-bold text-primary mb-2">Check Your Email</h1>
            <p className="text-secondary mb-6">
              We sent a sign-in link to <strong className="text-primary">{email}</strong>
            </p>

            <div
              className="rounded-lg p-4 mb-6"
              style={{ backgroundColor: 'var(--surface-secondary)' }}
            >
              <p className="text-sm text-secondary">
                Click the link in the email to create your account and log in.
                The link will expire in 15 minutes.
              </p>
            </div>

            <div className="space-y-3">
              <button
                onClick={handleResend}
                disabled={loading}
                className="btn-secondary w-full"
              >
                {loading ? 'Sending...' : 'Resend Email'}
              </button>

              <button
                onClick={() => {
                  setEmailSent(false);
                  setEmail('');
                }}
                className="text-sm text-secondary hover:text-primary transition-colors"
              >
                Use a different email
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Signup form
  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{
        background: 'linear-gradient(135deg, var(--surface-accent-subtle) 0%, var(--surface-accent) 100%)'
      }}
    >
      <ThemeToggleButton />
      <div className="w-full max-w-md">
        {/* Tier upgrade banner */}
        {tierInfo && (
          <div
            className="card mb-4 border-2"
            style={{ borderColor: tierInfo.color }}
          >
            <div className="flex items-center gap-3">
              <div
                className="w-10 h-10 rounded-lg flex items-center justify-center"
                style={{ backgroundColor: `${tierInfo.color}20` }}
              >
                <tierInfo.Icon className="w-5 h-5" style={{ color: tierInfo.color }} />
              </div>
              <div className="flex-1">
                <p className="font-medium text-primary">
                  Interested in {tierInfo.name}?
                </p>
                <p className="text-sm text-secondary">
                  Sign up free first, then upgrade to {tierInfo.price}
                </p>
              </div>
            </div>
          </div>
        )}

        <div className="card">
          <div className="text-center mb-8">
            <div className="flex justify-center mb-6">
              <Logo width={200} height={60} />
            </div>
            <h1 className="text-2xl font-bold text-primary mb-2">Create Free Account</h1>
            <p className="text-secondary">
              {tierInfo
                ? `Start free, then upgrade to ${tierInfo.name}`
                : 'Get started with 10 free document scans per month'
              }
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="name" className="block text-sm font-medium text-secondary mb-2">
                Full Name
              </label>
              <input
                id="name"
                name="name"
                type="text"
                value={name}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setName(e.target.value)}
                className="input"
                placeholder="Dr. Jane Smith"
                required
                minLength={2}
                maxLength={100}
                disabled={loading}
              />
            </div>

            <div>
              <label htmlFor="institution" className="block text-sm font-medium text-secondary mb-2">
                Institution
              </label>
              <input
                id="institution"
                name="institution"
                type="text"
                value={institution}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setInstitution(e.target.value)}
                className="input"
                placeholder="Stanford University"
                required
                minLength={2}
                maxLength={200}
                disabled={loading}
              />
            </div>

            <div>
              <label htmlFor="email" className="block text-sm font-medium text-secondary mb-2">
                Work Email <span className="text-xs text-tertiary">(educational institution required)</span>
              </label>
              <input
                id="email"
                name="email"
                type="email"
                value={email}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setEmail(e.target.value)}
                className="input"
                placeholder="faculty@university.edu"
                required
                disabled={loading}
              />
              <p className="mt-1 text-xs text-tertiary">
                Accepts .edu, .edu.au, .ac.uk, and other educational domains
              </p>
            </div>

            {error && (
              <div
                className="rounded-lg p-3 border flex items-start gap-2"
                style={{
                  backgroundColor: 'var(--surface-error-subtle)',
                  borderColor: 'var(--content-error)',
                }}
              >
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" style={{ color: 'var(--content-error)' }} />
                <p className="text-sm" style={{ color: 'var(--content-error)' }}>{error}</p>
              </div>
            )}

            <button
              type="submit"
              className="btn-primary w-full flex items-center justify-center gap-2"
              disabled={loading}
            >
              {loading ? (
                'Sending...'
              ) : (
                <>
                  Continue with Email
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          <div className="mt-6 text-center text-sm text-secondary">
            <p>Already have an account?</p>
            <Link
              to="/login"
              className="text-accent font-medium hover:opacity-80 transition-opacity"
            >
              Sign in
            </Link>
          </div>

          <div
            className="mt-6 rounded-lg p-4"
            style={{ backgroundColor: 'var(--surface-secondary)' }}
          >
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="w-4 h-4" style={{ color: 'var(--content-success)' }} />
              <h3 className="font-medium text-sm text-primary">Free Plan Includes:</h3>
            </div>
            <ul className="text-sm text-secondary space-y-1">
              <li>• 10 document scans per month</li>
              <li>• PDF, Word, Excel, PowerPoint</li>
              <li>• AI-powered alt text generation</li>
              <li>• Auto-remediation & fixes</li>
              <li>• Compliance reports</li>
            </ul>
          </div>
        </div>

        <div className="mt-6 text-center text-sm text-tertiary">
          <p>
            By signing up, you agree to our{' '}
            <a
              href={`${WEBSITE_URL}/terms`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:opacity-80 transition-opacity"
            >
              Terms of Service
            </a>
            {' '}and{' '}
            <a
              href={`${WEBSITE_URL}/privacy`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:opacity-80 transition-opacity"
            >
              Privacy Policy
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
