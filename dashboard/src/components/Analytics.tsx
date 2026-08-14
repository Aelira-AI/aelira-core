import { useEffect, useState } from 'react';

// ============================================================================
// Types
// ============================================================================

interface ConsentData {
  analytics?: boolean;
}

interface UmamiInstance {
  track: (eventOrOptions: string | { website: string; url: string }, data?: Record<string, unknown>) => void;
}

declare global {
  interface Window {
    umami?: UmamiInstance;
  }
}

// ============================================================================
// Constants
// ============================================================================

/**
 * Umami Analytics Configuration
 * These values should be set in your environment variables
 */
const UMAMI_WEBSITE_ID = import.meta.env.VITE_UMAMI_WEBSITE_ID || '';
const UMAMI_URL = import.meta.env.VITE_UMAMI_URL || '';

const CONSENT_COOKIE_NAME = 'aelira-cookie-consent';

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Check if analytics consent is given
 */
function hasAnalyticsConsent(): boolean {
  try {
    const stored = localStorage.getItem(CONSENT_COOKIE_NAME);
    if (!stored) return false;
    const consent: ConsentData = JSON.parse(stored);
    return consent.analytics === true;
  } catch {
    return false;
  }
}

// ============================================================================
// Component
// ============================================================================

/**
 * Analytics component that loads Umami tracking script
 * Only loads when user has consented to analytics cookies
 */
export function Analytics(): null {
  const [canLoad, setCanLoad] = useState<boolean>(false);
  const [scriptLoaded, setScriptLoaded] = useState<boolean>(false);

  useEffect(() => {
    // Check initial consent
    const checkConsent = (): void => {
      const allowed = hasAnalyticsConsent();
      setCanLoad(allowed);
    };

    checkConsent();

    // Listen for consent changes
    const handleConsentChange = (): void => {
      checkConsent();
    };

    window.addEventListener('cookieConsentChanged', handleConsentChange);

    return () => {
      window.removeEventListener('cookieConsentChanged', handleConsentChange);
    };
  }, []);

  // Load script when consent is given
  useEffect(() => {
    if (!canLoad || !UMAMI_WEBSITE_ID || scriptLoaded) return;

    // Check if script already exists - use timeout to avoid synchronous setState
    const existingScript = document.querySelector(`script[data-website-id="${UMAMI_WEBSITE_ID}"]`);
    if (existingScript) {
      // Use microtask to avoid synchronous setState in effect
      queueMicrotask(() => setScriptLoaded(true));
      return;
    }

    const script = document.createElement('script');
    script.src = `${UMAMI_URL}/script.js`;
    script.async = true;
    script.defer = true;
    script.setAttribute('data-website-id', UMAMI_WEBSITE_ID);
    script.setAttribute('data-domains', 'dashboard.example.com');

    script.onload = (): void => {
      setScriptLoaded(true);
    };

    document.head.appendChild(script);

    return () => {
      // Don't remove script on unmount - it needs to persist
    };
  }, [canLoad, scriptLoaded]);

  // This component doesn't render anything visible
  return null;
}

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * Track a custom event in Umami
 * Only sends if analytics consent is given
 *
 * @param eventName - Name of the event to track
 * @param eventData - Optional data to attach to the event
 *
 * @example
 * trackEvent('scan_started', { type: 'pdf' });
 * trackEvent('feature_used', { feature: 'bulk_upload' });
 */
export function trackEvent(eventName: string, eventData: Record<string, unknown> = {}): void {
  if (typeof window === 'undefined') return;

  // Check consent before tracking
  if (!hasAnalyticsConsent()) return;

  // Umami exposes a global umami object
  if (window.umami?.track) {
    window.umami.track(eventName, eventData);
  }
}

/**
 * Track a page view manually
 * Useful for SPA navigation that Umami might miss
 *
 * @param url - Optional URL to track (defaults to current path)
 */
export function trackPageView(url?: string): void {
  if (typeof window === 'undefined') return;

  // Check consent before tracking
  if (!hasAnalyticsConsent()) return;

  if (window.umami?.track && UMAMI_WEBSITE_ID) {
    window.umami.track({
      website: UMAMI_WEBSITE_ID,
      url: url || window.location.pathname,
    });
  }
}
