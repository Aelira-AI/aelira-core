import React, { useState, useEffect, ChangeEvent } from 'react';
import { Cookie, X, Settings, Check } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

interface CookieCategory {
  name: string;
  description: string;
  required: boolean;
}

interface CookiePreferences {
  functional: boolean;
  analytics: boolean;
}

interface StoredConsent extends CookiePreferences {
  essential: boolean;
  timestamp: number;
  version: string;
}

// ============================================================================
// Constants
// ============================================================================

const CONSENT_COOKIE_NAME = 'aelira-cookie-consent';
const CONSENT_VERSION = '1.0';

const COOKIE_CATEGORIES: Record<string, CookieCategory> = {
  essential: {
    name: 'Essential',
    description: 'Required for the dashboard to function. These cannot be disabled.',
    required: true,
  },
  functional: {
    name: 'Functional',
    description: 'Remember your preferences like theme and sidebar state.',
    required: false,
  },
  analytics: {
    name: 'Analytics',
    description: 'Help us understand how you use the dashboard to improve it.',
    required: false,
  },
};

// ============================================================================
// Helper Functions
// ============================================================================

function getConsent(): StoredConsent | null {
  try {
    const stored = localStorage.getItem(CONSENT_COOKIE_NAME);
    if (!stored) return null;
    const consent = JSON.parse(stored) as StoredConsent;
    if (consent.version !== CONSENT_VERSION) return null;
    return consent;
  } catch {
    return null;
  }
}

function saveConsent(consent: CookiePreferences): void {
  const fullConsent: StoredConsent = {
    essential: true,
    functional: consent.functional ?? false,
    analytics: consent.analytics ?? false,
    timestamp: Date.now(),
    version: CONSENT_VERSION,
  };
  localStorage.setItem(CONSENT_COOKIE_NAME, JSON.stringify(fullConsent));

  // Set a cookie so server can detect consent
  const expiryDate = new Date();
  expiryDate.setDate(expiryDate.getDate() + 365);
  document.cookie = `${CONSENT_COOKIE_NAME}=accepted; expires=${expiryDate.toUTCString()}; path=/; SameSite=Lax`;

  window.dispatchEvent(new CustomEvent('cookieConsentChanged', { detail: fullConsent }));
}

// ============================================================================
// Component
// ============================================================================

export function CookieBanner(): React.ReactElement | null {
  const [showBanner, setShowBanner] = useState<boolean>(false);
  const [showPreferences, setShowPreferences] = useState<boolean>(false);
  const [preferences, setPreferences] = useState<CookiePreferences>({
    functional: true,
    analytics: false,
  });

  useEffect(() => {
    const consent = getConsent();
    if (!consent) {
      // Small delay to avoid layout shift
      const timer = setTimeout(() => setShowBanner(true), 500);
      return () => clearTimeout(timer);
    } else {
      // Use microtask to avoid synchronous setState in effect
      queueMicrotask(() => {
        setPreferences({
          functional: consent.functional,
          analytics: consent.analytics,
        });
      });
    }
  }, []);

  const handleAcceptAll = (): void => {
    saveConsent({ functional: true, analytics: true });
    setShowBanner(false);
    setShowPreferences(false);
  };

  const handleRejectAll = (): void => {
    saveConsent({ functional: false, analytics: false });
    setShowBanner(false);
    setShowPreferences(false);
  };

  const handleSavePreferences = (): void => {
    saveConsent(preferences);
    setShowBanner(false);
    setShowPreferences(false);
  };

  const handleFunctionalChange = (e: ChangeEvent<HTMLInputElement>): void => {
    setPreferences({ ...preferences, functional: e.target.checked });
  };

  const handleAnalyticsChange = (e: ChangeEvent<HTMLInputElement>): void => {
    setPreferences({ ...preferences, analytics: e.target.checked });
  };

  if (!showBanner) return null;

  return (
    <>
      {/* Overlay for preferences modal. Purely decorative click-to-dismiss
          backdrop; the modal has an explicit "Close preferences" button for
          keyboard users, so this is hidden from the accessibility tree
          rather than faked up as an interactive control. */}
      {showPreferences && (
        <div
          className="fixed inset-0 bg-black/50 z-[9998]"
          role="presentation"
          aria-hidden="true"
          onClick={() => setShowPreferences(false)}
        />
      )}

      {/* Cookie Preferences Modal */}
      {showPreferences && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="cookie-preferences-title"
          className="fixed top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 z-[9999] w-full max-w-lg rounded-xl shadow-2xl p-6 max-h-[90vh] overflow-y-auto"
          style={{
            backgroundColor: 'var(--surface-primary)',
            border: '1px solid var(--border-primary)',
          }}
        >
          <div className="flex items-center justify-between mb-4">
            <h2 id="cookie-preferences-title" className="text-xl font-semibold text-primary">
              Cookie Preferences
            </h2>
            <button
              onClick={() => setShowPreferences(false)}
              className="p-1 rounded-lg hover:bg-[var(--surface-secondary)] transition-colors"
              aria-label="Close preferences"
            >
              <X className="w-5 h-5 text-secondary" />
            </button>
          </div>

          <p className="text-sm text-secondary mb-6">
            We use cookies to enhance your experience. You can customize which cookies you allow
            below. For more information, see our{' '}
            <a
              href="https://example.com/privacy"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--content-accent)] hover:underline"
            >
              Privacy Policy
            </a>
            .
          </p>

          <div className="space-y-3">
            {/* Essential - Always On */}
            <div className="p-4 rounded-lg" style={{ backgroundColor: 'var(--surface-secondary)' }}>
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-primary">{COOKIE_CATEGORIES.essential.name}</span>
                <span
                  className="text-xs px-2 py-1 rounded font-medium"
                  style={{
                    backgroundColor: 'var(--surface-accent-subtle)',
                    color: 'var(--content-accent)',
                  }}
                >
                  Always On
                </span>
              </div>
              <p className="text-sm text-secondary">{COOKIE_CATEGORIES.essential.description}</p>
            </div>

            {/* Functional */}
            <label
              className="block p-4 rounded-lg cursor-pointer transition-colors"
              style={{ backgroundColor: 'var(--surface-secondary)' }}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-primary">{COOKIE_CATEGORIES.functional.name}</span>
                <input
                  type="checkbox"
                  checked={preferences.functional}
                  onChange={handleFunctionalChange}
                  className="w-5 h-5 rounded cursor-pointer"
                  style={{ accentColor: 'var(--content-accent)' }}
                />
              </div>
              <p className="text-sm text-secondary">{COOKIE_CATEGORIES.functional.description}</p>
            </label>

            {/* Analytics */}
            <label
              className="block p-4 rounded-lg cursor-pointer transition-colors"
              style={{ backgroundColor: 'var(--surface-secondary)' }}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-primary">{COOKIE_CATEGORIES.analytics.name}</span>
                <input
                  type="checkbox"
                  checked={preferences.analytics}
                  onChange={handleAnalyticsChange}
                  className="w-5 h-5 rounded cursor-pointer"
                  style={{ accentColor: 'var(--content-accent)' }}
                />
              </div>
              <p className="text-sm text-secondary">{COOKIE_CATEGORIES.analytics.description}</p>
            </label>
          </div>

          <div className="flex gap-3 mt-6">
            <button
              onClick={handleSavePreferences}
              className="flex-1 px-4 py-2.5 rounded-lg font-medium transition-opacity hover:opacity-90"
              style={{
                backgroundColor: 'var(--accent-primary)',
                color: 'white',
              }}
            >
              Save Preferences
            </button>
            <button
              onClick={() => setShowPreferences(false)}
              className="flex-1 px-4 py-2.5 rounded-lg font-medium transition-colors"
              style={{
                backgroundColor: 'var(--surface-secondary)',
                color: 'var(--content-primary)',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Main Cookie Banner */}
      {!showPreferences && (
        <div
          role="dialog"
          aria-modal="false"
          aria-labelledby="cookie-banner-title"
          aria-describedby="cookie-banner-description"
          className="fixed bottom-0 left-0 right-0 z-[9998] p-4 shadow-2xl"
          style={{
            backgroundColor: 'var(--surface-primary)',
            borderTop: '1px solid var(--border-primary)',
          }}
        >
          <div className="max-w-4xl mx-auto">
            <div className="flex flex-col sm:flex-row sm:items-center gap-4">
              <div className="flex items-start gap-3 flex-1">
                <div
                  className="p-2 rounded-lg flex-shrink-0"
                  style={{ backgroundColor: 'var(--surface-accent-subtle)' }}
                >
                  <Cookie className="w-5 h-5" style={{ color: 'var(--content-accent)' }} />
                </div>
                <div>
                  <h2 id="cookie-banner-title" className="font-semibold text-primary mb-1">
                    Cookie Notice
                  </h2>
                  <p id="cookie-banner-description" className="text-sm text-secondary">
                    We use cookies to remember your preferences and improve your experience.{' '}
                    <a
                      href="https://example.com/privacy"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[var(--content-accent)] hover:underline"
                    >
                      Learn more
                    </a>
                  </p>
                </div>
              </div>

              <div className="flex flex-wrap gap-2 sm:flex-nowrap">
                <button
                  onClick={() => setShowPreferences(true)}
                  className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg transition-colors"
                  style={{
                    color: 'var(--content-secondary)',
                  }}
                >
                  <Settings className="w-4 h-4" />
                  Preferences
                </button>
                <button
                  onClick={handleRejectAll}
                  className="px-4 py-2 text-sm font-medium rounded-lg transition-colors"
                  style={{
                    backgroundColor: 'var(--surface-secondary)',
                    color: 'var(--content-primary)',
                  }}
                >
                  Reject All
                </button>
                <button
                  onClick={handleAcceptAll}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-opacity hover:opacity-90"
                  style={{
                    backgroundColor: 'var(--accent-primary)',
                    color: 'white',
                  }}
                >
                  <Check className="w-4 h-4" />
                  Accept All
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
