import React, { ChangeEvent, FormEvent, useState } from 'react';
import { ArrowRight, Check, Loader2, Mail, Moon, ShieldCheck, Sun } from 'lucide-react';
import { Link } from 'react-router-dom';
import { apiClient } from '../api/client';
import { Logo } from '../components/Logo';
import { useTheme } from '../context/theme-context';
import { consumeInvitationToken } from '../utils/invitationToken';

type InvitationView = 'ready' | 'submitting' | 'success' | 'error';

interface AcceptInvitationResponse {
  success: boolean;
  already_accepted?: boolean;
  outcome?: string;
  status?: string;
  role?: string;
  message?: string;
}

interface ApiError {
  response?: {
    status?: number;
    data?: {
      detail?: string;
    };
  };
}

// Capturing at module evaluation keeps the token ahead of the app-wide
// analytics component and survives React StrictMode's double initialization.
const initialInvitation =
  typeof window !== 'undefined' && window.location.pathname === '/accept-invitation'
    ? consumeInvitationToken(window.location, window.history)
    : { token: '', hadToken: false };

function errorMessageFor(error: unknown): string {
  const apiError = error as ApiError;
  const status = apiError.response?.status;
  const detail = apiError.response?.data?.detail?.toLowerCase() || '';

  if (detail.includes('email') && detail.includes('match')) {
    return 'That email address does not match this invitation. Use the address the invitation was sent to.';
  }

  if (
    status === 404 ||
    status === 410 ||
    detail.includes('expired') ||
    detail.includes('revoked') ||
    detail.includes('invalid') ||
    detail.includes('no longer available')
  ) {
    return 'This invitation link is no longer valid. Ask the administrator who created the department to send a new one.';
  }

  if (status === 409 || detail.includes('already exists')) {
    return 'This email cannot accept the invitation. Contact your department administrator for help.';
  }

  return 'We could not complete the invitation. Please try again, or ask your department administrator for a new link.';
}

// HERO: the institutional handoff seal turns account setup into a clear transfer of trust.
export function AcceptInvitation(): React.ReactElement {
  const { theme, toggleTheme } = useTheme();
  const [email, setEmail] = useState<string>('');
  const [name, setName] = useState<string>('');
  const [view, setView] = useState<InvitationView>(
    initialInvitation.token ? 'ready' : 'error'
  );
  const [error, setError] = useState<string>(
    initialInvitation.token
      ? ''
      : 'This invitation link is incomplete or no longer valid. Ask your department administrator for a new one.'
  );
  const [wasReplay, setWasReplay] = useState<boolean>(false);
  const [isAdminAccess, setIsAdminAccess] = useState<boolean>(false);
  const [retryAllowed, setRetryAllowed] = useState<boolean>(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!initialInvitation.token || view === 'submitting') return;

    setError('');
    setView('submitting');

    try {
      const response = await apiClient.post<AcceptInvitationResponse>(
        '/auth/accept-invitation',
        {
          token: initialInvitation.token,
          email: email.trim().toLowerCase(),
          name: name.trim() || null,
        },
        { _skipApiKeyAuth: true }
      );

      const replayed =
        response.data.already_accepted === true ||
        response.data.outcome === 'already_accepted' ||
        response.data.status === 'already_accepted';
      setWasReplay(replayed);
      setIsAdminAccess(
        replayed || response.data.role === 'admin' || response.data.role === 'super_admin'
      );
      setView('success');
    } catch (requestError: unknown) {
      const message = errorMessageFor(requestError);
      setError(message);
      setRetryAllowed(
        message.startsWith('That email address') ||
        message.startsWith('We could not complete')
      );
      setView('error');
    }
  };

  const showForm = view === 'ready' || view === 'submitting';

  return (
    <main
      className="relative min-h-screen overflow-hidden px-4 py-8 sm:px-6 lg:px-8"
      style={{
        backgroundColor: 'var(--surface-secondary)',
        backgroundImage:
          'radial-gradient(circle at 12% 12%, var(--surface-accent) 0, transparent 28%), var(--paper-texture)',
        color: 'var(--content-primary)',
      }}
    >
      <button
        type="button"
        onClick={toggleTheme}
        className="fixed bottom-5 right-5 z-20 rounded-full border p-3 transition-transform duration-200 hover:-translate-y-0.5 motion-reduce:transition-none"
        style={{
          backgroundColor: 'var(--surface-primary)',
          borderColor: 'var(--border-primary)',
          boxShadow: 'var(--shadow-md)',
          color: 'var(--content-accent)',
        }}
        aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      >
        {theme === 'dark' ? (
          <Sun className="h-5 w-5" aria-hidden="true" />
        ) : (
          <Moon className="h-5 w-5" aria-hidden="true" />
        )}
      </button>

      <div className="mx-auto grid min-h-[calc(100vh-4rem)] max-w-5xl items-center gap-0 lg:grid-cols-[0.88fr_1.12fr]">
        <section
          className="relative overflow-hidden rounded-t-3xl px-7 py-10 sm:px-10 lg:rounded-l-3xl lg:rounded-tr-none lg:px-12 lg:py-16"
          style={{
            backgroundColor: 'var(--surface-dark)',
            color: 'var(--content-inverse)',
            boxShadow: 'var(--shadow-xl)',
          }}
          aria-labelledby="handoff-heading"
        >
          <div
            className="absolute -right-20 -top-20 h-64 w-64 rounded-full border opacity-30"
            style={{ borderColor: 'var(--surface-accent-strong)' }}
            aria-hidden="true"
          />
          <div
            className="absolute -right-8 -top-8 h-40 w-40 rounded-full border opacity-40"
            style={{ borderColor: 'var(--accent-sage)' }}
            aria-hidden="true"
          />

          <div className="relative">
            <div
              className="mb-12 inline-flex rounded-sm px-4 py-3"
              style={{ backgroundColor: 'var(--surface-primary)' }}
            >
              <Logo width={150} height={45} />
            </div>
            <p
              className="mb-4 text-xs font-semibold uppercase tracking-[0.22em]"
              style={{ color: 'var(--accent-sage)' }}
            >
              Secure invitation
            </p>
            <h1 id="handoff-heading" className="max-w-sm text-4xl leading-tight sm:text-5xl">
              Your workspace is ready.
            </h1>
            <p className="mt-6 max-w-sm text-base leading-relaxed opacity-80">
              Accept the invitation with the same institutional email address it was sent to.
              You will then sign in normally and continue to the area your role permits.
            </p>

            <div className="mt-10 flex items-center gap-4" aria-hidden="true">
              <div
                className="grid h-14 w-14 place-items-center rounded-full border-2"
                style={{
                  borderColor: 'var(--accent-sage)',
                  backgroundColor: 'var(--accent-sage-light)',
                  color: 'var(--accent-sage)',
                }}
              >
                <ShieldCheck className="h-7 w-7" />
              </div>
              <div className="h-px flex-1 opacity-40" style={{ backgroundColor: 'var(--content-inverse)' }} />
              <span className="font-heading text-lg italic opacity-80">Verified invitation</span>
            </div>
          </div>
        </section>

        <section
          className="rounded-b-3xl border px-7 py-10 sm:px-12 lg:rounded-r-3xl lg:rounded-bl-none lg:px-16 lg:py-16"
          style={{
            backgroundColor: 'var(--surface-primary)',
            borderColor: 'var(--border-primary)',
            boxShadow: 'var(--shadow-xl)',
          }}
          aria-labelledby="invitation-form-heading"
        >
          {showForm && (
            <div className="animate-[fade-in_0.35s_ease-out] motion-reduce:animate-none">
              <p className="mb-2 text-sm font-semibold uppercase tracking-[0.16em] text-accent">
                One final step
              </p>
              <h2 id="invitation-form-heading" className="text-3xl">Accept your invitation</h2>
              <p className="mt-3 text-secondary">
                Confirm the invited email. Your name is optional and can be added later.
              </p>

              <form onSubmit={handleSubmit} className="mt-8 space-y-6">
                <div>
                  <label htmlFor="invitation-email" className="mb-2 block text-sm font-semibold text-secondary">
                    Institutional email
                  </label>
                  <input
                    id="invitation-email"
                    type="email"
                    className="input min-h-12"
                    value={email}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => setEmail(event.target.value)}
                    autoComplete="email"
                    inputMode="email"
                    placeholder="you@university.edu"
                    required
                    disabled={view === 'submitting'}
                  />
                </div>

                <div>
                  <label htmlFor="invitation-name" className="mb-2 block text-sm font-semibold text-secondary">
                    Name <span className="font-normal text-tertiary">(optional)</span>
                  </label>
                  <input
                    id="invitation-name"
                    type="text"
                    className="input min-h-12"
                    value={name}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => setName(event.target.value)}
                    autoComplete="name"
                    maxLength={200}
                    disabled={view === 'submitting'}
                  />
                </div>

                <button
                  type="submit"
                  className="btn-primary flex min-h-12 w-full items-center justify-center gap-2"
                  disabled={view === 'submitting'}
                >
                  {view === 'submitting' ? (
                    <>
                      <Loader2 className="h-5 w-5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                      Completing handoff…
                    </>
                  ) : (
                    <>
                      Accept invitation
                      <ArrowRight className="h-5 w-5" aria-hidden="true" />
                    </>
                  )}
                </button>
              </form>

              <p className="mt-6 flex items-start gap-2 text-sm text-tertiary">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                The invitation is bound to one department and one email address.
              </p>
            </div>
          )}

          {view === 'success' && (
            <div className="animate-[fade-in_0.35s_ease-out] motion-reduce:animate-none" role="status" aria-live="polite">
              <div
                className="mb-8 grid h-16 w-16 place-items-center rounded-full"
                style={{
                  backgroundColor: 'var(--accent-sage-light)',
                  color: 'var(--accent-sage)',
                }}
              >
                <Check className="h-8 w-8" aria-hidden="true" />
              </div>
              <p className="mb-2 text-sm font-semibold uppercase tracking-[0.16em]" style={{ color: 'var(--content-success)' }}>
                Invitation complete
              </p>
              <h2 id="invitation-form-heading" className="text-3xl">
                {wasReplay ? 'Access already active' : 'Your access is ready'}
              </h2>
              <p className="mt-4 text-secondary">
                {wasReplay
                  ? 'This invitation was already completed. Continue to sign in to your account.'
                  : isAdminAccess
                    ? 'Your administrator account has been created. Use your email to request a secure magic link and open the admin area.'
                    : 'Your account has been created. Use your email to request a secure magic link and continue to the dashboard.'}
              </p>
              <Link
                to={isAdminAccess ? '/login?next=%2Fadmin' : '/login?next=%2Fdashboard'}
                className="btn-primary mt-8 flex min-h-12 w-full items-center justify-center gap-2"
              >
                <Mail className="h-5 w-5" aria-hidden="true" />
                Continue to secure sign in
              </Link>
            </div>
          )}

          {view === 'error' && (
            <div className="animate-[fade-in_0.35s_ease-out] motion-reduce:animate-none" role="alert">
              <div
                className="mb-7 h-1 w-20"
                style={{ backgroundColor: 'var(--accent-terracotta)' }}
                aria-hidden="true"
              />
              <p className="mb-2 text-sm font-semibold uppercase tracking-[0.16em]" style={{ color: 'var(--content-warning)' }}>
                Invitation unavailable
              </p>
              <h2 id="invitation-form-heading" className="text-3xl">We could not complete the invitation</h2>
              <p className="mt-4 text-secondary">{error}</p>
              {retryAllowed && (
                <button
                  type="button"
                  className="btn-secondary mt-8 min-h-12 w-full"
                  onClick={() => {
                    setError('');
                    setView('ready');
                  }}
                >
                  Check the details and try again
                </button>
              )}
              <Link to="/login" className="mt-6 inline-flex items-center gap-2 font-semibold text-accent">
                Already have access? Sign in
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
